/**
 * PhaseVocoderProcessor — AudioWorklet pitch shifter using an FFT-based phase vocoder.
 *
 * Changes pitch by factor P = 2^(semitones/12) without altering playback speed.
 *
 * Algorithm per frame:
 *   1. Read N windowed input samples  → analysis FFT
 *   2. Estimate true instantaneous frequency for each bin
 *   3. Remap bins by pitch factor P   (compress spectrum for pitch up)
 *   4. Accumulate synthesis phases    → synthesis IFFT
 *   5. Overlap-add windowed output
 *
 * Parameters:
 *   N    = 2048 samples  (~42 ms at 48 kHz)  — FFT / analysis window
 *   H    = 512  samples  — hop size (75 % overlap)
 *   norm = 1/1.5         — Hann² COLA normalisation for 4× overlap
 *   latency ≈ N samples  (~42 ms)
 *
 * Messages accepted from the main thread:
 *   { pitchFactor: number }   — e.g. 1.0 = no shift, 2^(2/12)≈1.122 = +2 st
 *   { reset: true }           — clear phase state (avoids glitch on abrupt change)
 */

// ─── Radix-2 Cooley-Tukey in-place FFT ───────────────────────────────────────

function fftInPlace(re, im) {
  const N = re.length;

  // Bit-reversal permutation
  let j = 0;
  for (let i = 1; i < N; i++) {
    let bit = N >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      let t = re[i]; re[i] = re[j]; re[j] = t;
      t = im[i]; im[i] = im[j]; im[j] = t;
    }
  }

  // Butterfly stages
  for (let len = 2; len <= N; len <<= 1) {
    const half = len >> 1;
    const ang  = -2 * Math.PI / len;
    const wBRe = Math.cos(ang), wBIm = Math.sin(ang);
    for (let i = 0; i < N; i += len) {
      let wRe = 1, wIm = 0;
      for (let k = 0; k < half; k++) {
        const uRe = re[i+k],      uIm = im[i+k];
        const vRe = re[i+k+half]*wRe - im[i+k+half]*wIm;
        const vIm = re[i+k+half]*wIm + im[i+k+half]*wRe;
        re[i+k]      = uRe + vRe;  im[i+k]      = uIm + vIm;
        re[i+k+half] = uRe - vRe;  im[i+k+half] = uIm - vIm;
        const t = wRe*wBRe - wIm*wBIm;
        wIm = wRe*wBIm + wIm*wBRe;  wRe = t;
      }
    }
  }
}

function ifftInPlace(re, im) {
  const N = re.length;
  for (let i = 0; i < N; i++) im[i] = -im[i];
  fftInPlace(re, im);
  for (let i = 0; i < N; i++) { re[i] /= N; im[i] = -im[i] / N; }
}

// ─── Phase Vocoder AudioWorklet ───────────────────────────────────────────────

class PhaseVocoderProcessor extends AudioWorkletProcessor {
  constructor() {
    super();

    this._pitchFactor = 1.0;

    const N = 2048;   // FFT / analysis window size (power of 2)
    const H = N >> 2; // hop = N/4 → 75 % overlap
    this._N = N;
    this._H = H;

    // Hann window (applied for both analysis and synthesis)
    this._win = new Float32Array(N);
    for (let i = 0; i < N; i++) {
      this._win[i] = 0.5 * (1 - Math.cos(2 * Math.PI * i / N));
    }

    // COLA normalisation: Hann² with 4× overlap sums to 1.5 at every sample
    this._norm = 1 / 1.5;

    this._numCh = 0;
    this._state  = null;
    this._BS     = N * 8;          // ring-buffer size (must be >> N)

    // Countdown until the next analysis frame (prime with N so we wait for
    // a full window before the first frame is computed)
    this._samplesTilFrame = N;

    this.port.onmessage = ({ data }) => {
      if (typeof data.pitchFactor === 'number') {
        this._pitchFactor = Math.max(0.25, Math.min(4.0, data.pitchFactor));
      }
      if (data.reset && this._state) {
        for (const s of this._state) {
          s.prevPhase.fill(0);
          s.synthPhase.fill(0);
        }
      }
    };
  }

  // ── Per-channel state allocation ─────────────────────────────────────────────

  _initState(numCh) {
    const { _N: N, _BS: BS } = this;
    this._numCh = numCh;
    this._samplesTilFrame = N; // restart countdown

    this._state = Array.from({ length: numCh }, () => ({
      // Input ring buffer
      inBuf:  new Float32Array(BS),
      inWrite: 0,
      inRead:  0,   // next analysis-frame start position

      // Phase vocoder persistent state
      prevPhase:  new Float32Array(N),  // analysis phase from last frame
      synthPhase: new Float32Array(N),  // accumulated synthesis phase

      // Scratch arrays (pre-allocated to avoid GC in the hot path)
      re:   new Float32Array(N),
      im:   new Float32Array(N),
      outRe: new Float32Array(N),
      outIm: new Float32Array(N),
      mag:  new Float32Array((N >> 1) + 1),
      freq: new Float32Array((N >> 1) + 1),

      // Output ring buffer.
      // Pre-filled with N silent samples so outRead can start at 0 while
      // outWrite starts at N — this gives an N-sample output latency.
      outBuf:   new Float32Array(BS),
      outWrite: N,
      outRead:  0,
    }));
  }

  // ── AudioWorklet render callback ─────────────────────────────────────────────

  process(inputs, outputs) {
    const inp = inputs[0], out = outputs[0];
    if (!inp?.length || !out?.length) return true;

    const ch  = Math.min(inp.length, out.length);
    const blk = inp[0].length; // typically 128 samples

    // Passthrough when pitch factor is unity (avoids latency and CPU cost)
    if (Math.abs(this._pitchFactor - 1.0) < 0.001) {
      for (let c = 0; c < ch; c++) out[c].set(inp[c]);
      return true;
    }

    if (this._numCh !== ch) this._initState(ch);

    const { _N: N, _H: H, _BS: BS, _win: win, _norm: norm } = this;

    // 1. Write new input samples into ring buffers
    for (let c = 0; c < ch; c++) {
      const s = this._state[c];
      for (let i = 0; i < blk; i++) s.inBuf[(s.inWrite + i) % BS] = inp[c][i];
      s.inWrite = (s.inWrite + blk) % BS;
    }

    // 2. Process phase-vocoder frames (shared trigger across all channels so
    //    all channels stay in sync)
    this._samplesTilFrame -= blk;
    while (this._samplesTilFrame <= 0) {
      this._samplesTilFrame += H;
      for (let c = 0; c < ch; c++) this._processFrame(this._state[c], N, H, BS, win, norm);
    }

    // 3. Read processed output from ring buffers
    for (let c = 0; c < ch; c++) {
      const s = this._state[c];
      for (let i = 0; i < blk; i++) {
        const p = (s.outRead + i) % BS;
        out[c][i] = s.outBuf[p];
        s.outBuf[p] = 0; // clear after reading to keep accumulator clean
      }
      s.outRead = (s.outRead + blk) % BS;
    }

    return true; // keep processor alive
  }

  // ── Phase-vocoder frame ──────────────────────────────────────────────────────

  _processFrame(s, N, H, BS, win, norm) {
    const P      = this._pitchFactor;
    const TWO_PI = 2 * Math.PI;
    const halfN  = N >> 1;

    const re = s.re, im = s.im, outRe = s.outRe, outIm = s.outIm;
    const mag = s.mag, freq = s.freq;

    // ── Analysis ──────────────────────────────────────────────────────────────

    im.fill(0);
    for (let i = 0; i < N; i++) re[i] = s.inBuf[(s.inRead + i) % BS] * win[i];
    s.inRead = (s.inRead + H) % BS;

    fftInPlace(re, im);

    // Estimate true instantaneous frequency for each analysis bin
    const expectedInc = TWO_PI * H / N; // expected phase increment per hop
    for (let k = 0; k <= halfN; k++) {
      mag[k] = Math.sqrt(re[k]*re[k] + im[k]*im[k]);

      const ph = Math.atan2(im[k], re[k]);
      let dp   = ph - s.prevPhase[k] - k * expectedInc;
      s.prevPhase[k] = ph;

      // Wrap to (−π, +π]
      dp -= TWO_PI * Math.round(dp / TWO_PI);

      // True frequency (radians per sample)
      freq[k] = k * TWO_PI / N + dp / H;
    }

    // ── Pitch-shift bin remapping ─────────────────────────────────────────────
    //
    // Output bin k draws content from input bin k/P.
    //   P > 1  → spectrum compressed (lower bins fill higher ones) → pitch up
    //   P < 1  → spectrum expanded  (higher bins fill lower ones)  → pitch down

    outRe.fill(0); outIm.fill(0);

    for (let k = 0; k <= halfN; k++) {
      const srcF = k / P;
      const srcK = Math.floor(srcF);
      const frac = srcF - srcK;

      if (srcK >= 0 && srcK < halfN) {
        // Linear interpolation of magnitude and frequency
        const m = mag[srcK]  * (1 - frac) + mag[srcK + 1]  * frac;
        const f = (freq[srcK] * (1 - frac) + freq[srcK + 1] * frac) * P;

        // Accumulate synthesis phase
        s.synthPhase[k] += f * H;

        // Winner-take-all: only write if this is the loudest contributor
        // (avoids cancellation when multiple input bins map to the same output bin)
        const prevMag = Math.sqrt(outRe[k]*outRe[k] + outIm[k]*outIm[k]);
        if (m > prevMag) {
          outRe[k] = m * Math.cos(s.synthPhase[k]);
          outIm[k] = m * Math.sin(s.synthPhase[k]);
        }
      }
    }

    // Restore conjugate symmetry so the IFFT produces a real signal
    outIm[0] = outIm[halfN] = 0; // DC and Nyquist are real-valued
    for (let k = 1; k < halfN; k++) {
      outRe[N - k] =  outRe[k];
      outIm[N - k] = -outIm[k];
    }

    // ── Synthesis ─────────────────────────────────────────────────────────────

    ifftInPlace(outRe, outIm);

    // Apply synthesis Hann window, normalise, and overlap-add into output ring buffer
    for (let i = 0; i < N; i++) {
      const p = (s.outWrite + i) % BS;
      s.outBuf[p] += outRe[i] * win[i] * norm;
    }
    s.outWrite = (s.outWrite + H) % BS;
  }
}

registerProcessor('pitch-shift-processor', PhaseVocoderProcessor);
