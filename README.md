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

## Left/right smooth audio contours

The visualizer draws two continuously connected, smoothly varying sound-wave outlines (one on each side of the bouncing image), synchronized with audio amplitude and bass energy. Each contour has a gentle glow and drop shadow. The code explicitly removes the top and bottom frame lines and every top/bottom bar from both the MP4 renderer and browser preview. Drag-to-crop, cover beat bounce and Git auto-sync remain unchanged. Existing exported MP4s do not change automatically; create a new export after updating.

## Gentle moving background

The blurred source image now has a slow ambient horizontal/vertical drift behind the centered artwork. The renderer builds an oversized, dark, blurred backdrop once, then smoothly pans a 1280x720 crop over it without exposing empty edges. The live preview uses a matching slow CSS animation. The central cover, smooth left/right waveform, crop controls and auto-sync scripts are unchanged.

## Refined background and waveform edges

The moving backdrop now uses a milder Gaussian blur (radius 22 rather than 38), while the browser preview uses 12px instead of 22px. MP4 wave contours are drawn at 2x resolution then downsampled with LANCZOS to suppress jagged pixels. The left and right waves now begin and end 30px away from the square's top/bottom corners, using smooth eased tapers; no top or bottom waves are added. The white picture border, beat bounce, drag-to-crop and auto-sync are preserved.

## Waveform edge clipping and full-video tint

The glowing side waveforms are masked out inside the animated cover rectangle, including a 2px safety margin, so their glow/shadow cannot leak over the picture at the edge. The browser preview uses the same outside-only clipping. Settings now include an overlay color picker and a 0–100% opacity slider. The preview displays the tint immediately; the MP4 renderer applies it as the last compositing step to the entire frame (background, central image, border, and waves). An opacity of 0 preserves the original image. Auto-sync scripts remain unchanged.

## ZIV watermark and matching thumbnail downloads

The uploaded ZIV logo is stored as a transparent image in `assets/ziv-logo.webp` and displayed near the upper-right corner of exported MP4s and the live preview. Its gentle glow keeps it readable without covering the center photo. The existing color overlay tints the entire finished video, including the watermark.

Every completed render now also saves a **1280×720 JPG thumbnail** featuring the chosen square image and its white outline over the blurred background, with the same tint setting. The thumbnail intentionally excludes the audio waves and watermark. Clicking **Download video + thumbnail (.zip)** retrieves a single ZIP containing `music-video.mp4` and `thumbnail.jpg`; separate MP4-only and JPG-only download links are available under it. Both assets remain in `data/visualizers/outputs/` after rendering. The previous auto-sync launchers and scripts remain intact.
