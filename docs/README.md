# nerdBar-Karaoke

A FOSS karaoke system for downloading and playing karaoke tracks from YouTube forked from PiKaraoke.

## Features

- Docker-Based Deployment
- Web-based karaoke interface
- Web-based song search
- QR code for easy access to the karaoke interface from mobile devices
- Automatic YouTube downloading
- Song queue management
- Admin interface for managing songs and settings
- User song addition cooldown
- Volume Normalization
- Multi-language support (English, German)
- User voting on songs in the queue (upvote/downvote)
- Message of the day (MOTD) 
- Queue open/close system with optional closing time

## Roadmap
- [ ] Implement non-Username-based user identification (via individualized access link; Autorefreshed QR code).
- [ ] swap out background video
- [ ] Give pitch control to users when adding song
- [ ] change pitch without song restart
- [ ] implement tempo display/control
- [ ] implement tags (ideally autotaged) for genres, moods, languages, artits, duet
- [ ] implement grouping by tags
- [ ] Genre-Mode: Only songs of a certain genre are shown and can be added
- [ ] Push-Notification to requester when their song is up next / playing
- [ ] display on bar to display queue in LUK
- [ ] Autoskip silence at beginning and end of tracks
- (?) Song rating system (star rating or similar) to reflect quality of caraoke track; Maybe difficulty rating?
- (?) NFC Tag on Microphones to add Singer Nameplates to Overlay
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

