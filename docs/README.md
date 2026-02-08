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

## Roadmap
- Implement non-Username-based user identification (via cookies)
- add Up-/Downvote system for songs in queue to allow users to influence order of songs in queue
- shadow-remove songs from queue (keeps them at the end of the queue, shows up/downvote score one less than the lowsest real score in queue)
- swap out background video

## Installation

For detailed installation instructions, see the main project documentation.

## Configuration

Configure by copying `.env.sample` to `.env` and editing.

## Docker Installation

To run PiKaraoke in Docker:

```bash
docker compose up --build
```

