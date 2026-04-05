# nerdBar-Karaoke

A FOSS karaoke system for downloading and playing karaoke tracks from YouTube forked from PiKaraoke.

## Features

- Docker-Based Deployment
- Web-based karaoke interface
- Web-based song search
- QR code for easy access to the karaoke interface from mobile devices
- Automatic YouTube downloading
- Song queue management with **persistent queue** (survives server restarts)
- Admin interface for managing songs and settings
- **Admin log viewer** — real-time dashboard for monitoring application logs
- User song addition cooldown
- Volume Normalization
- Multi-language support (English, German)
- User voting on songs in the queue (upvote/downvote)
- Fair queue mode (users with fewer songs in the queue get priority)
- **Fair queue admin pinning** — admins can drag songs to a fixed position that survives re-ordering; configurable pin modes (keep position / pin to previous song); unpin button in song options
- Message of the day (MOTD)
- Queue open/close system with optional closing time
- NFC-based Microphone tracking (song will wait for you, show singers on display)
- OSC-Controll to toggle mic-effects on an external mixer
- Drag-n-drop Upload of local karaoke video-files
- **Co-singers / multi-requestees** — tag multiple singers when adding a song to the queue; all names appear on the queue display
- **Song likes** — users can ♥ songs from the queue and song browser; liked songs are tracked per user
  - Weighted randomizer: songs liked by more users are more likely to be picked by the random song adder
  - Browse filter: view only your liked songs
  - Taste-match: compare which songs you and another user both like

## Roadmap

- [ ] Implement non-Username-based user identification (via individualized access link; Autorefreshed QR code).

- [ ] Give pitch control to users when adding song
- [ ] change pitch without song restart (requires switch to Web Audio API for Streaming)
- [ ] implement multi chanel playback (seperate backing, vocal and clicker tracks)
- [ ] implement tempo display/control

- [ ] implement tags (ideally autotaged) for genres, moods, languages, artits, duet (stored in file metadata)
- [ ] implement grouping by tags
- [ ] store artist in metadata not in title
- [ ] autoadd tags from last.fm to metadata
- [ ] Genre-Mode: Only songs of a certain genre are shown and can be added

- [ ] Push-Notification to requester when their song is up next / playing

- [ ] queue-view to display in bar

- [ ] Anonymus queue mode (only admins see names)

- (?) Song rating system (star rating or similar) to reflect quality of caraoke track; Maybe difficulty rating?

- (?) UI Themes

- (?) User Metrics (most requested songs, most active users, etc.)

- (?) Button to open original song on spotify; Option to export queue as spotify playlist

- (???) integrate lighting to sync to music / title change

## Installation

For detailed installation instructions, see the main project documentation.

## Deployment Configuration

Configure by copying `.env.sample` to `.env` and editing.

## Docker Installation

To run PiKaraoke in Docker:

```bash
docker compose up --build
```

