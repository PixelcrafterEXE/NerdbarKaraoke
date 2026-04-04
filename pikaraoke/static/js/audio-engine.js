/**
 * KaraokeAudioEngine — Web Audio API based real-time audio processing.
 *
 * Provides:
 *  • Pitch shifting (semitones) — independent of tempo, via AudioWorklet
 *  • Tempo adjustment (speed multiplier) — independent of pitch, via
 *    preservesPitch=true + playbackRate
 *
 * NOTE: Silence detection was previously attempted via an AnalyserNode, but
 * the video is delivered as HLS via HLS.js (Media Source Extensions).  Browsers
 * intentionally do not route MSE audio through the Web Audio graph, so the
 * AnalyserNode always returns all-zeros (-Infinity dB).  Silence detection is
 * therefore handled server-side via ffprobe and exposed through the now_playing
 * socket event.  See splash.js for the client-side seek logic.
 *
 * Architecture:
 *  ┌──────────┐    ┌──────────────┐    ┌──────────────┐    ┌─────┐
 *  │  <video> │───>│ MediaElement  │───>│ PitchShift   │───>│ Dest│
 *  │ element  │    │   Source      │    │ Worklet      │    │     │
 *  └──────────┘    └──────────────┘    └──────────────┘    └─────┘
 *
 *  Tempo:  video.playbackRate = tempo,  video.preservesPitch = true
 *  Pitch:  AudioWorklet resamples grains by 2^(semitones/12)
 */

class KaraokeAudioEngine {
  /**
   * @param {HTMLVideoElement} videoElement
   * @param {object} [options]
   * @param {number}   [options.semitones=0]
   * @param {number}   [options.tempo=1.0]
   */
  constructor(videoElement, options = {}) {
    this.video = videoElement;
    this.semitones = options.semitones || 0;
    this.tempo = options.tempo || 1.0;

    // Web Audio nodes
    this._audioCtx = null;
    this._sourceNode = null;
    this._pitchNode = null;   // AudioWorkletNode for pitch shifting
    this._connected = false;
    this._disposed = false;

    // Apply initial tempo (pitch is applied after worklet loads)
    this._applyTempo();
  }

  // --- Initialisation ---

  /**
   * Initialise the Web Audio graph.
   * Must be called after a user gesture (autoplay policy).
   */
  async init() {
    if (this._connected || this._disposed) return;

    console.log('[AudioEngine] init — semitones=' + this.semitones + ' tempo=' + this.tempo);

    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) {
        console.warn('[AudioEngine] Web Audio API not supported');
        return;
      }

      this._audioCtx = new AudioCtx();

      console.log('[AudioEngine] AudioContext state before resume: ' + this._audioCtx.state);
      await this._audioCtx.resume();
      console.log('[AudioEngine] AudioContext state after resume: ' + this._audioCtx.state);

      // Source from <video>
      this._sourceNode = this._audioCtx.createMediaElementSource(this.video);

      // Connect directly to destination (pitch worklet splices in below)
      this._sourceNode.connect(this._audioCtx.destination);
      this._connected = true;
      console.log('[AudioEngine] basic graph connected');

      // Load pitch worklet in background — splices between source and destination
      this._audioCtx.audioWorklet.addModule('/static/js/pitch-shift-processor.js')
        .then(() => {
          if (this._disposed) return;
          const pitchNode = new AudioWorkletNode(this._audioCtx, 'pitch-shift-processor');
          // Splice pitch node in: source → pitchNode → destination
          this._sourceNode.disconnect(this._audioCtx.destination);
          this._sourceNode.connect(pitchNode);
          pitchNode.connect(this._audioCtx.destination);
          this._pitchNode = pitchNode;
          this._applyPitch();
          console.log('[AudioEngine] pitch worklet loaded and spliced in');
        })
        .catch((e) => {
          console.warn('[AudioEngine] AudioWorklet unavailable, pitch shifting disabled:', e.message);
        });

    } catch (e) {
      console.error('[AudioEngine] init failed:', e);
    }
  }

  // --- Pitch ---

  /**
   * Set pitch shift in semitones.  Instant, no re-encoding.
   * @param {number} semitones  (-12 to +12 typical)
   */
  setPitch(semitones) {
    this.semitones = semitones;
    this._applyPitch();
  }

  getPitch() { return this.semitones; }

  /** @private Send pitch factor to the AudioWorklet. */
  _applyPitch() {
    if (this._pitchNode) {
      const factor = Math.pow(2, this.semitones / 12);
      // Reset phase state first to avoid a glitch when pitch changes
      this._pitchNode.port.postMessage({ reset: true });
      this._pitchNode.port.postMessage({ pitchFactor: factor });
    }
  }

  // --- Tempo ---

  /**
   * Set tempo multiplier.  Instant, no re-encoding.
   * Uses the browser's built-in time-stretch (preservesPitch = true)
   * so pitch stays constant while speed changes.
   * @param {number} tempo  (0.25 to 4.0)
   */
  setTempo(tempo) {
    this.tempo = Math.max(0.25, Math.min(4.0, tempo));
    this._applyTempo();
  }

  getTempo() { return this.tempo; }

  /** @private Apply tempo via playbackRate with pitch preserved. */
  _applyTempo() {
    if (!this.video) return;
    this.video.preservesPitch = true;
    if ('webkitPreservesPitch' in this.video) {
      this.video.webkitPreservesPitch = true;
    }
    this.video.playbackRate = this.tempo;
  }

  // --- Lifecycle ---

  reset() {
    this.semitones = 0;
    this.tempo = 1.0;
    this._applyPitch();
    this._applyTempo();
  }

  async resume() {
    if (this._audioCtx && this._audioCtx.state === 'suspended') {
      await this._audioCtx.resume();
    }
  }

  dispose() {
    this._disposed = true;

    try { if (this._sourceNode) this._sourceNode.disconnect(); } catch (_) {}
    try { if (this._pitchNode) this._pitchNode.disconnect(); } catch (_) {}

    this._sourceNode = null;
    this._pitchNode = null;

    if (this._audioCtx) {
      this._audioCtx.close().catch(function(){});
      this._audioCtx = null;
    }

    if (this.video) {
      this.video.playbackRate = 1.0;
      this.video.preservesPitch = true;
    }

    this._connected = false;
    console.log('[AudioEngine] disposed');
  }
}

window.KaraokeAudioEngine = KaraokeAudioEngine;
