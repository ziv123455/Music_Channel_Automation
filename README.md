# Music Visualizer Studio

This is a fresh, minimal Flask application. The old AI-generating music dashboard, compilation tools, channel manager, thumbnail generator and other features were removed from the main branch.

## Features
- Upload an existing MP3/WAV/FLAC/M4A/OGG song
- Upload a PNG/JPG/WEBP image
- Crop image into a centered square with dark blurred background
- White/pastel audio-reactive spikes on the left and right edges
- Customize spike color and sensitivity
- Generate and download a 1280x720 30fps MP4 with the uploaded music

## Start on Windows
Install Python 3.11+ and FFmpeg on PATH. Run start.bat (or start-all.bat). Open http://127.0.0.1:5050.

MP4 exports are in data/visualizers/outputs. Only one render runs at a time. The job state clears when Flask restarts but completed MP4 files remain on disk. Only upload songs and pictures you have rights to publish.

The original full software has been preserved in GitHub branch backup-before-visualizer-only-reset-2026-11-15.

## Beat bounce and interactive square crop

After selecting a picture, drag inside the square crop editor to choose the exact part of the original artwork. The Zoom slider lets you crop more closely. The preview and rendered MP4 use the same source coordinates. During rendering, kick-band (low-pass) audio energy makes the square artwork pulse subtly (up to 6.5% larger); the edge spikes still respond to the full song. The app and existing Git auto-sync scripts remain separate.

## Responsive perimeter waveform and image quality

The waveform now traces all four moving edges of the bass-bouncing square, rather than staying anchored to the old static box. The image renderer keeps the original selected crop at full source resolution, caches high-quality LANCZOS-resized bounce sizes, uses improved H.264 export quality (CRF 17), and previews the chosen crop on an 800px canvas. The audio player provides an animated in-browser preview when playing. Low-resolution uploaded pictures can still look blurry if zoomed too far; start with a square image of at least 1000 pixels when available. The original Git auto-sync scripts remain untouched.

## Left/right equalizer bars

The visualizer now draws 95 thin, evenly spaced horizontal bars on each side of the bouncing image, synchronized with audio. Bars move along with the changing image boundaries, with soft colored glow and a subtle shadow. No bars are drawn above or below the square. The draggable crop, image bounce and Git auto-sync are unchanged.
