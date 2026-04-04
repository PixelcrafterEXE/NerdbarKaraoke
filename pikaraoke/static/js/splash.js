// Global variables
var socket = io();
var mouseTimer = null,
  cursorVisible = false;
var nowPlaying = {};
var octopusInstance = null;
var showMenu = false;
var menuButtonVisible = false;
var autoplayConfirmed = false;
var volume = 0.85;
var playbackStartTimeout = 10000;
var bgMediaResumeDelay = 2000;
var isScoreShown = false;
var hasBgVideo = PikaraokeConfig.hasBgVideo;
var currentVideoUrl = null;
var hlsInstance = null;
var idleTime = 0;
var screensaverTimeoutSeconds = PikaraokeConfig.screensaverTimeout;
var bg_playlist = [];
var bgMediaResumeTimeout = null;
const scoreReviews = PikaraokeConfig.scorePhrases;
var isMaster = false;
var uiScale = null;
var motdResizeListenerAttached = false;
var audioEngine = null;

// Server-side silence boundary state (set per-song from now_playing data)
var _silenceLeadingEnd = null;      // seconds — seek past this on song start
var _silenceTrailingStart = null;   // seconds — end song when currentTime reaches this
var _leadingSeekDone = false;
var _trailingSilenceHandler = null; // bound timeupdate listener (removed on song end)

// Browser detection
const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
const isMobileSafari = isSafari && (/iPhone|iPad|iPod/i.test(navigator.userAgent) || navigator.maxTouchPoints > 1);
const isChrome = /chrome/i.test(navigator.userAgent) && !/edg/i.test(navigator.userAgent);
const isFirefox = /firefox/i.test(navigator.userAgent);
const isEdge = /edg/i.test(navigator.userAgent);
const isSupportedBrowser = isSafari || isChrome || isFirefox || isEdge;

// Support functions below

const isMediaPlaying = (media) =>
  !!(
    media.currentTime > 0 &&
    !media.paused &&
    !media.ended &&
    media.readyState > 2
  );

const formatTime = (seconds) => {
  if (isNaN(seconds)) {
    return "00:00";
  }
  const totalSeconds = Math.floor(seconds);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const secs = totalSeconds % 60;
  const formattedMinutes = String(minutes).padStart(2, "0");
  const formattedSeconds = String(secs).padStart(2, "0");
  return `${formattedMinutes}:${formattedSeconds}`;
}

const testAutoplayCapability = async () => {
  // Test if autoplay with audio is allowed using a real video file
  try {
    const testVideo = document.createElement('video');
    testVideo.playsInline = true;
    testVideo.muted = true;  // Start muted (always allowed)
    testVideo.src = "/static/video/test_autoplay.mp4";

    // Wait for video to be ready
    await new Promise((resolve, reject) => {
      testVideo.onloadeddata = resolve;
      testVideo.onerror = reject;
    });

    await testVideo.play();
    // Now try to unmute - this is the real test
    testVideo.muted = false;
    testVideo.volume = 0.01;

    // Brief delay to let browser enforce policy
    await new Promise(resolve => setTimeout(resolve, 500));

    // Check if browser paused or muted the video
    if (testVideo.muted || testVideo.paused) {
      testVideo.pause();
      $('#permissions-modal').addClass('is-active');
    } else {
      testVideo.pause();
      handleConfirmation();
    }
  } catch (e) {
    // Autoplay blocked
    console.log("Autoplay error thrown", e);
    $('#permissions-modal').addClass('is-active');
  }
};

const handleConfirmation = () => {
  $('#permissions-modal').removeClass('is-active');
  autoplayConfirmed = true;
  updateBackgroundMediaState(true);
  loadNowPlaying();
};

const hideVideo = () => {
  $("#video-container").hide();
}

const endSong = async (reason = null, showScore = false) => {
  if (showScore && !PikaraokeConfig.disableScore) {
    isScoreShown = true;
    await startScore("/static/");
    isScoreShown = false;
  }
  currentVideoUrl = null;
  if (hlsInstance) {
    hlsInstance.destroy();
    hlsInstance = null;
  }
  // Dispose audio engine (releases Web Audio resources)
  if (audioEngine) {
    audioEngine.dispose();
    audioEngine = null;
  }
  const video = getVideoPlayer();
  video.pause();
  $("#video-source").attr("src", "");
  video.load();
  hideVideo();
  if (isMaster) {
    socket.emit("end_song", reason);
  } else {
    console.log("Slave active (read-only): skipping end_song emission");
  }
}

const getBackgroundMusicPlayer = () => document.getElementById('background-music');
const getBackgroundVideoPlayer = () => document.getElementById('bg-video');
const getVideoPlayer = () => $("#video")[0]

const getNextBgMusicSong = () => {
  let currentSong = getBackgroundMusicPlayer().getAttribute('src');
  let nextSong = bg_playlist[0];
  if (currentSong) {
    let currentIndex = bg_playlist.indexOf(currentSong);
    if (currentIndex >= 0 && currentIndex < bg_playlist.length - 1) {
      nextSong = bg_playlist[currentIndex + 1];
    }
  }
  return nextSong;
}

const playBGMusic = async (play) => {
  const audio = getBackgroundMusicPlayer();
  if (play) {
    if (PikaraokeConfig.disableBgMusic) return;
    if (!autoplayConfirmed) return;
    if (bg_playlist.length === 0) return;

    if (!audio.getAttribute('src')) audio.setAttribute('src', getNextBgMusicSong());

    if (isMediaPlaying(audio)) return;
    audio.volume = 0;
    if (audio.readyState <= 2) await audio.load();
    await audio.play().catch(e => console.log("Autoplay blocked (music)"));
    $(audio).animate({ volume: PikaraokeConfig.bgMusicVolume }, 2000);
  } else {
    if (audio) {
      $(audio).animate({ volume: 0 }, 2000, () => audio.pause());
    }
  }
}

const playBGVideo = async (play) => {
  const bgVideo = getBackgroundVideoPlayer();
  const bgVideoContainer = $('#bg-video-container');

  if (play) {
    if (PikaraokeConfig.disableBgVideo) return;
    if (!autoplayConfirmed) return;

    if (isMediaPlaying(bgVideo)) return;
    $("#bg-video").attr("src", "/stream/bg_video");
    if (bgVideo.readyState <= 2) await bgVideo.load();
    bgVideo.play().catch(() => console.log("Autoplay blocked (video)"));
    bgVideoContainer.fadeIn(2000);
  } else {
    if (bgVideo && isMediaPlaying(bgVideo)) {
      bgVideo.pause();
      bgVideoContainer.fadeOut(2000);
    }
  }
}

const shouldBackgroundMediaPlay = () => {
  return autoplayConfirmed &&
    !nowPlaying.now_playing &&
    !nowPlaying.up_next;
};

const updateBackgroundMediaState = (immediate = false) => {
  // Clear any pending resume
  if (bgMediaResumeTimeout) {
    clearTimeout(bgMediaResumeTimeout);
    bgMediaResumeTimeout = null;
  }

  if (shouldBackgroundMediaPlay()) {
    if (immediate) {
      playBGMusic(true);
      if (hasBgVideo) playBGVideo(true);
    } else {
      bgMediaResumeTimeout = setTimeout(() => {
        bgMediaResumeTimeout = null;
        if (shouldBackgroundMediaPlay()) {
          playBGMusic(true);
          if (hasBgVideo) playBGVideo(true);
        }
      }, bgMediaResumeDelay);
    }
  } else {
    playBGMusic(false);
    playBGVideo(false);
  }
};

const flashNotification = (message, categoryClass) => {
  const sn = $("#splash-notification");
  if (sn.html()) return;
  sn.html(message);
  sn.addClass(categoryClass);
  sn.fadeIn();
  setTimeout(() => {
    sn.fadeOut();
    setTimeout(() => {
      sn.html("");
      sn.removeClass(categoryClass);
    }, 450);
  }, 3000);
}

const setupScreensaver = () => {
  if (screensaverTimeoutSeconds > 0) {
    setInterval(() => {
      let screensaver = document.getElementById('screensaver');
      let video = getVideoPlayer();
      if (isMediaPlaying(video) || cursorVisible) {
        idleTime = 0;
      }
      if (idleTime >= screensaverTimeoutSeconds) {
        if (screensaver.style.visibility === 'hidden') {
          screensaver.style.visibility = 'visible';
          playBGVideo(false);
          startScreensaver(); // depends on upstream screensaver.js import
        }
        if (idleTime > screensaverTimeoutSeconds + 36000) idleTime = screensaverTimeoutSeconds;
      } else {
        if (screensaver.style.visibility === 'visible') {
          screensaver.style.visibility = 'hidden';
          stopScreensaver(); // depends on upstream screensaver.js import
          updateBackgroundMediaState(true);
        }
      }
      idleTime++;
    }, 1000)
  }
}

const handleNowPlayingUpdate = (np) => {
  nowPlaying = np;
  
  // Handle waiting for microphone
  if (np.waiting_for_microphone) {
    const waitInfo = np.waiting_for_microphone;
    const waitingText = PikaraokeConfig.translations.waitingForMicrophone
      .replace('%s', waitInfo.waiting_for_user)
      .replace('{}', waitInfo.waiting_for_user);
    const waitingHtml = `
      <div class="has-text-warning is-size-4">
        <i class="icon icon-mic-1"></i> ${waitingText}
      </div>
      <div class="has-text-info is-size-5" style="margin-top: 10px;">
        ${PikaraokeConfig.translations.song}: ${waitInfo.waiting_for_song}
      </div>
      <div class="has-text-grey is-size-6" style="margin-top: 5px;">
        ${PikaraokeConfig.translations.timeRemaining}: ${waitInfo.time_remaining}s
      </div>
    `;
    $("#waiting-for-microphone").html(waitingHtml).fadeIn();
  } else {
    $("#waiting-for-microphone").fadeOut();
  }
  
  if (np.now_playing) {

    // Handle updating now playing HTML with requester in brackets
    let nowPlayingHtml = `<span class="has-text-grey-light" style="font-size: 0.8em;">(${np.now_playing_user})</span> <span>${np.now_playing}</span> `;
    if (np.now_playing_transpose !== 0) {
      nowPlayingHtml += `<span class='is-size-6 has-text-success'><b>Key</b>: ${getSemitonesLabel(np.now_playing_transpose)} </span>`;
    }
    if (np.now_playing_tempo != null && np.now_playing_tempo !== 1.0) {
      nowPlayingHtml += `<span class='is-size-6 has-text-info'><b>Tempo</b>: ${parseFloat(np.now_playing_tempo).toFixed(1)}x </span>`;
    }
    $("#now-playing-song").html(nowPlayingHtml);
    
    // Update microphone holders display with dark saturated colors
    if (np.microphone_assignments && PikaraokeConfig.showMicrophoneStatus) {
      const colorMap = PikaraokeConfig.microphoneColors || {};
      let micHtml = '';
      for (const [micId, user] of Object.entries(np.microphone_assignments)) {
        if (user) {
          const colorHex = colorMap[micId] || '#999';
          micHtml += `<i class="icon icon-mic-1" style="color: ${colorHex}; font-size: 1.2em; margin-right: 5px;" title="${user}"></i><span style="color: ${colorHex}; margin-right: 10px;">${user}</span> `;
        }
      }
      $("#microphone-holders").html(micHtml);
    }
    
    $("#now-playing").fadeIn();
  } else {
    $("#now-playing").fadeOut();
  }
  const updateMotdContainer = (containerSelector, textSelector, shouldShow) => {
    const motdContainer = $(containerSelector);
    if (!motdContainer.length) return;
    const motdText = $(textSelector);
    if (motdText.length) {
      const newMotd = np.motd || "";
      if (motdText.text() !== newMotd) {
        motdText.text(newMotd);
        applyMotdMarquee();
      }
    }
    if (shouldShow) {
      motdContainer.fadeIn();
    } else {
      motdContainer.fadeOut();
    }
  };
  updateMotdContainer("#motd-container", "#motd-text", !!np.now_playing);
  updateMotdContainer("#motd-under-logo", "#motd-text-under-logo", !np.now_playing);
  if (np.up_next) {
    $("#up-next-song").html(np.up_next);
    $("#up-next-singer").html(np.next_user);
    $("#up-next").fadeIn();
  } else {
    $("#up-next").fadeOut();
  }

  // Update bg music and video state
  if (np.now_playing || np.up_next) {
    idleTime = 0;
  }
  updateBackgroundMediaState();

  const video = getVideoPlayer();

  // Setup ASS subtitle file if found
  const subtitleUrl = np.now_playing_subtitle_url;
  if (octopusInstance) {
    octopusInstance.dispose();
    octopusInstance = null;
  }
  if (subtitleUrl && video) {
    const options = {
      video: video,
      subUrl: subtitleUrl,
      fonts: ["/static/fonts/Arial.ttf", "/static/fonts/DroidSansFallback.ttf"],
      debug: true,
      workerUrl: "/static/js/subtitles-octopus-worker.js"
    };
    try {
      octopusInstance = new SubtitlesOctopus(options);
      if (uiScale) {
        // Find the canvas created by SubtitlesOctopus (sibling of the video)
        const canvas = video.parentNode.querySelector('canvas');
        if (canvas) {
          canvas.style.transform = `scale(${uiScale})`;
          canvas.style.transformOrigin = 'bottom center';
        }
      }
    } catch (e) { console.error(e); }
  }

  if (np.now_playing_url && np.now_playing_url !== currentVideoUrl) {
    currentVideoUrl = np.now_playing_url;
    const streamUrl = np.now_playing_url;

    // Reset audio engine for new song (will be re-initialized on play event)
    if (audioEngine) {
      audioEngine.dispose();
      audioEngine = null;
    }

    // --- Server-side silence boundaries ---
    // Store the ffprobe-detected boundaries for this song.  The actual seek
    // (leading) and end-song trigger (trailing) are applied once the video is
    // playing, because we cannot seek before media is loaded.
    _silenceLeadingEnd = (np.now_playing_leading_silence_end > 0.5)
      ? np.now_playing_leading_silence_end : null;
    _silenceTrailingStart = (np.now_playing_trailing_silence_start > 0)
      ? np.now_playing_trailing_silence_start : null;
    _leadingSeekDone = false;

    // Remove any trailing-silence handler left from previous song
    if (_trailingSilenceHandler) {
      video.removeEventListener('timeupdate', _trailingSilenceHandler);
      _trailingSilenceHandler = null;
    }

    if (_silenceLeadingEnd) {
      console.log('[silence] leading silence ends at', _silenceLeadingEnd.toFixed(2) + 's — will seek on canplay');
    }
    if (_silenceTrailingStart) {
      console.log('[silence] trailing silence starts at', _silenceTrailingStart.toFixed(2) + 's — will end song');
    }

    $("#video-source").attr("src", "");
    video.load();
    $("#video-source").attr("src", streamUrl);

    if (streamUrl.endsWith('.m3u8')) {
      const useNativeHLS = video.canPlayType('application/vnd.apple.mpegurl') && !isChrome && !isEdge && !isMobileSafari;
      if (useNativeHLS) {
        video.src = streamUrl;
      } else {
        if (hlsInstance) { hlsInstance.destroy(); hlsInstance = null; }
        hlsInstance = new Hls({ startPosition: 0 });
        hlsInstance.loadSource(streamUrl);
        hlsInstance.attachMedia(video);
      }
    }

    video.load();
    if (volume !== np.volume) {
      volume = np.volume;
      video.volume = volume;
    }

    const duration = $("#duration");
    if (np.now_playing_duration) {
      duration.text(` / ${formatTime(np.now_playing_duration)}`);
      duration.show();
    } else {
      duration.hide();
    }

    $("#video-container").show();

    video.play().catch(err => {
      console.error('Play failed:', err);
      // Retry once if it was an autoplay block
      setTimeout(() => video.play(), 1000);
    });

    // Leading silence seek: once the video can play (metadata + data available),
    // seek past the silent intro detected by ffprobe.
    if (_silenceLeadingEnd !== null) {
      const leadingSeekHandler = () => {
        video.removeEventListener('canplay', leadingSeekHandler);
        if (!_leadingSeekDone && _silenceLeadingEnd !== null) {
          _leadingSeekDone = true;
          console.log('[silence] seeking past leading silence to', _silenceLeadingEnd.toFixed(2) + 's');
          video.currentTime = _silenceLeadingEnd;
        }
      };
      video.addEventListener('canplay', leadingSeekHandler);
    }

    // Trailing silence: register a timeupdate handler that ends the song
    // when playback reaches the trailing silence boundary.
    if (_silenceTrailingStart !== null) {
      _trailingSilenceHandler = () => {
        if (_silenceTrailingStart !== null && video.currentTime >= _silenceTrailingStart) {
          console.log('[silence] trailing silence reached at', video.currentTime.toFixed(2) + 's — ending song');
          video.removeEventListener('timeupdate', _trailingSilenceHandler);
          _trailingSilenceHandler = null;
          _silenceTrailingStart = null;
          if (isMaster) {
            endSong("complete", true);
          }
        }
      };
      video.addEventListener('timeupdate', _trailingSilenceHandler);
    }

    if (np.now_playing_position && isMediaPlaying(video)) {
      if (Math.abs(video.currentTime - np.now_playing_position) > 2) {
        console.log("Syncing to server position:", np.now_playing_position);
        video.currentTime = np.now_playing_position;
      }
    }

    setTimeout(() => {
      if (!isMediaPlaying(video) && !video.paused) {
        endSong("failed to start");
      }
    }, playbackStartTimeout);
  }
}

async function loadNowPlaying() {
  const data = await $.get("/now_playing");
  handleNowPlayingUpdate(JSON.parse(data));
}

const setupOverlayMenus = () => {
  if (PikaraokeConfig.hideOverlay) {
    $('#bottom-container').hide();
    $('#top-container').hide();
  }
  $("#menu a").fadeOut(); // start hidden
  const triggerInactivity = () => {
    mouseTimer = null;
    document.body.style.cursor = 'none';
    cursorVisible = false;
    $("#menu a").fadeOut();
    menuButtonVisible = false;
  };

  document.onmousemove = function () {
    if (mouseTimer) window.clearTimeout(mouseTimer);
    if (!cursorVisible) {
      document.body.style.cursor = 'default';
      cursorVisible = true;
    }
    if (!menuButtonVisible) {
      $("#menu a").fadeIn();
      menuButtonVisible = true;
    }
    mouseTimer = window.setTimeout(triggerInactivity, 5000);
  };

  // Set initial state to hidden
  triggerInactivity();
  $('#menu a').click(function () {
    if (showMenu) {
      $('#menu-container').hide();
      $('#menu-container iframe').attr('src', '');
      showMenu = false;
    } else {
      setUserCookie();
      $("#menu-container").show();
      $("#menu-container iframe").attr("src", "/");
      showMenu = true;
    }
  });
  $('#menu-background').click(function () {
    if (showMenu) {
      $(".navbar-burger").click();
    }
  });
}

const initAudioEngine = async () => {
  const video = getVideoPlayer();
  if (audioEngine) {
    audioEngine.dispose();
  }
  audioEngine = new KaraokeAudioEngine(video, {
    semitones: nowPlaying.now_playing_transpose || 0,
    tempo: nowPlaying.now_playing_tempo || 1.0,
    // Silence detection is handled server-side (ffprobe); the Web Audio
    // analyser cannot see HLS/MSE audio, so we disable it here.
  });
  await audioEngine.init();
  await audioEngine.resume();
};

const setupVideoPlayer = () => {
  $('#video-container').hide();
  const video = getVideoPlayer();
  video.addEventListener("play", async () => {
    $("#video-container").show();
    // Initialize Web Audio engine on first play (requires user gesture)
    if (!audioEngine) {
      await initAudioEngine();
    } else {
      await audioEngine.resume();
    }
    if (isMaster) {
      setTimeout(() => { socket.emit("start_song") }, 1200);
    }
  });

  // Master reports playback position to server
  setInterval(() => {
    if (isMaster && isMediaPlaying(video)) {
      socket.emit("playback_position", video.currentTime);
    }
  }, 1000);

  video.addEventListener("ended", () => { endSong("complete", true); });
  video.addEventListener("timeupdate", (e) => { $("#current").text(formatTime(video.currentTime)); });
  $("#video source")[0].addEventListener("error", (e) => {
    if (isMediaPlaying(video)) {
      endSong("error while playing");
    }
  });
  window.addEventListener(
    'beforeunload',
    function (event) {
      if (isMediaPlaying(video)) {
        endSong("splash screen closed");
      }
    },
    true
  );
}

const setupBackgroundMusicPlayer = () => {
  $.get("/bg_playlist", function (data) {
    if (data) bg_playlist = data;
  });
  const bgMusic = getBackgroundMusicPlayer();
  bgMusic.addEventListener("ended", async () => {
    bgMusic.setAttribute('src', getNextBgMusicSong());
    await bgMusic.load();
    await bgMusic.play();
  });
}

const handleUnsupportedBrowser = () => {
  if (!isSupportedBrowser) {
    let modalContents = document.getElementById("permissions-modal-content");
    let warningMessage = document.createElement("p");
    warningMessage.classList.add("notification", "is-warning");
    warningMessage.innerHTML =
      PikaraokeConfig.translations.unsupportedBrowser;
    modalContents.prepend(warningMessage);
  }
}

const setupSocketEvents = () => {
  socket.on('connect', () => {
    console.log('Socket connected');
    socket.emit("register_splash");
  });
  socket.on('splash_role', (role) => {
    isMaster = (role === "master");
    console.log("Splash role assigned:", role, isMaster ? "(Master active)" : "(Slave active - read-only)");
  });
  socket.on('connect_error', (error) => {
    console.error('Connection error:', error);
    flashNotification(PikaraokeConfig.translations.socketConnectionLost, "is-danger");
  });
  socket.on('disconnect', (reason) => {
    console.warn('Socket disconnected:', reason);
    flashNotification(PikaraokeConfig.translations.socketConnectionLost, "is-danger");
  });
  socket.on('pause', () => {
    const video = getVideoPlayer();
    const currVolume = video.volume;
    if (!video.paused) {
      $(video).animate({ volume: 0 }, 1000, () => {
        video.pause();
        video.volume = currVolume;
      });
    }
  });
  socket.on('play', () => {
    const video = getVideoPlayer();
    const currVolume = video.volume;
    if (video.paused) {
      video.play();
      video.volume = 0;
      $(video).animate({ volume: currVolume }, 1000);
    }
  });
  socket.on('skip', (reason) => {
    const video = getVideoPlayer();
    const currVolume = video.volume;
    if (isMediaPlaying(video)) {
      $(video).animate({ volume: 0 }, 1000, () => {
        video.pause();
        video.volume = currVolume;
        hideVideo();
      });
    } else {
      video.pause();
      hideVideo();
    }
  });
  socket.on('volume', (val) => {
    const video = getVideoPlayer();
    if (val === "up") {
      video.volume = Math.min(1, video.volume + 0.1);
    } else if (val === "down") {
      video.volume = Math.max(0, video.volume - 0.1);
    } else {
      video.volume = val;
    }
  });
  socket.on('restart', () => {
    const video = getVideoPlayer();
    video.currentTime = 0;
    if (video.paused) video.play();
  });
  socket.on("notification", (data) => {
    const notification = data.split("::");
    const message = notification[0];
    const categoryClass = notification.length > 1 ? notification[1] : "is-primary";
    flashNotification(message, categoryClass);
    if (isMaster) {
      socket.emit("clear_notification");
    }
  });
  socket.on("now_playing", handleNowPlayingUpdate);

  socket.on("set_pitch", (semitones) => {
    console.log("set_pitch received:", semitones);
    if (audioEngine) {
      audioEngine.setPitch(semitones);
    }
  });

  socket.on("set_tempo", (tempo) => {
    console.log("set_tempo received:", tempo);
    if (audioEngine) {
      audioEngine.setTempo(tempo);
    }
  });

  socket.on("playback_position", (position) => {
    if (!isMaster) {
      const video = getVideoPlayer();
      if (isMediaPlaying(video)) {
        if (Math.abs(video.currentTime - position) > 2) {
          console.log("Slave drifting, syncing position to:", position);
          video.currentTime = position;
        }
      }
    }
  });
}

const handleSocketRecovery = () => {
  // A socket may disconnect if the tab is backgrounded for a while
  // Reconnect and configure event listeners when tab becomes visible again
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === 'visible') {
      autoplayConfirmed && loadNowPlaying();
      if (!socket.connected) {
        socket = io();
        setupSocketEvents();
      }
    }
  });
}

const setupUIScaling = () => {
  const urlParams = new URLSearchParams(window.location.search);
  const rawScale = urlParams.get('scale');
  if (!rawScale) return;
  uiScale = parseFloat(rawScale) || 1;

  const scaleTargets = [
    { selector: '#logo-container img.logo', origin: null },
    { selector: '#top-container', origin: 'top right' },
    { selector: '#ap-container', origin: 'top left' },
    { selector: '#qr-code', origin: 'bottom left' },
    { selector: '#motd-container', origin: 'bottom center' },
    { selector: '#motd-under-logo', origin: 'center' },
    { selector: '#up-next', origin: 'bottom right' },
    { selector: '#dvd', origin: null },
    { selector: '#your-score-text', origin: null },
    { selector: '#score-number-text', origin: null },
    { selector: '#score-review-text', origin: null },
    { selector: '#splash-notification', origin: 'top left' },
  ];

  scaleTargets.forEach(({ selector, origin }) => {
    const el = document.querySelector(selector);
    if (el) {
      el.style.transform = `scale(${uiScale})`;
      if (origin) el.style.transformOrigin = origin;
    }
  });
}

const applyMotdMarqueeFor = (container, text) => {
  if (!container || !text) return;

  text.classList.remove('motd-scroll');
  text.style.setProperty('--motd-scroll-distance', '0px');
  text.style.setProperty('--motd-scroll-duration', '12s');

  const containerWidth = container.clientWidth;
  const textWidth = text.scrollWidth;
  if (textWidth > containerWidth) {
    const distance = textWidth - containerWidth;
    const duration = Math.max(8, Math.min(40, Math.round(distance / 60)));
    text.style.setProperty('--motd-scroll-distance', `${distance}px`);
    text.style.setProperty('--motd-scroll-duration', `${duration}s`);
    text.classList.add('motd-scroll');
  }
}

const applyMotdMarquee = () => {
  applyMotdMarqueeFor(
    document.getElementById('motd-container'),
    document.getElementById('motd-text')
  );
  applyMotdMarqueeFor(
    document.getElementById('motd-under-logo'),
    document.getElementById('motd-text-under-logo')
  );
}

const setupMotdMarquee = () => {
  applyMotdMarquee();
  if (motdResizeListenerAttached) return;
  motdResizeListenerAttached = true;
  let resizeTimeout = null;
  window.addEventListener('resize', () => {
    if (resizeTimeout) clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(applyMotdMarquee, 200);
  });
}

// Document ready procedures

$(function () {
  // Setup various features and listeners
  setupUIScaling();
  setupScreensaver();
  setupOverlayMenus();
  setupVideoPlayer();
  setupBackgroundMusicPlayer();
  setupMotdMarquee();

  // Handle browser compatibility
  handleUnsupportedBrowser();
  testAutoplayCapability();
  setInterval(() => {
    loadNowPlaying().catch(() => {});
  }, 5000);
});


// Setup sockets and recovery outside of document ready to prevent race conditions
setupSocketEvents();
handleSocketRecovery();

// Fallback: if socket connected before listeners were attached, register now
if (socket.connected) {
  console.log('Socket already connected, registering splash...');
  socket.emit("register_splash");
}
