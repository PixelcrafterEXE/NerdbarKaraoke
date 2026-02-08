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
- Implement non-Username-based user identification (via individualized access link; Autorefreshed QR code).
- swap out background video, find better suiting background music
- Give pitch control to users when adding song
- implement tempo display/control
- fix mobile queue display


## Installation

For detailed installation instructions, see the main project documentation.

## Deployment Configuration

Configure by copying `.env.sample` to `.env` and editing.

## Docker Installation

To run PiKaraoke in Docker:

```bash
docker compose up --build
```

