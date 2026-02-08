# nerdBar-Karaoke

A FOSS karaoke system for downloading and playing karaoke tracks from YouTube forked from PiKaraoke.

## Features

- Docker-Based Deployment
- Web-based karaoke interface
- Web-based song search
- Automatic YouTube downloading
- Song queue management
- Admin interface for managing songs and settings
- User song addition cooldown
- Volume Normalization
- Multi-language support (English, German)
- User voting on songs in the queue (upvote/downvote)

## Roadmap
- Implement non-Username-based user identification (via cookies)
- swap out background video
- Ability to close song queue adding
- Calculate total duration of queue and show it in the UI
- ability to set closing time. If this time is reached, no more songs can be added to the queue, but the already added songs will still be played. This allows for example to close the queue 30 minutes before the end of an event, so that the event can end on time.
- encrypt admin password

## Installation

For detailed installation instructions, see the main project documentation.

## Deployment Configuration

Configure by copying `.env.sample` to `.env` and editing.

## Docker Installation

To run PiKaraoke in Docker:

```bash
docker compose up --build
```

