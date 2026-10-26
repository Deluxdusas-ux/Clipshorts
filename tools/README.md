# YouTube clip workflow

This is an on-demand GitHub Actions workflow, not an always-running website.
It downloads an accessible YouTube video, transcribes it on the runner, selects
ten non-overlapping moments, burns original-language captions into vertical
MP4s, and packages the clips for download.

## Run it

1. Put this project in a GitHub repository and enable GitHub Actions.
2. Open **Actions → Create YouTube Shorts → Run workflow**.
3. Enter an HTTPS YouTube link, select the Whisper model, and confirm you own
   the video or have permission to clip it.
4. When the run succeeds, download the `youtube-shorts` artifact. It contains
   `shorts_output.zip` with ten MP4s and a JSON clip manifest.

The clips are 1080×1920 H.264/AAC MP4s, 18–45 seconds long, with subtitles
burned in. Captions stay in the spoken language; they are not translated.

## Cost and limits

- The workflow uses CPU transcription and local transcript-scoring rules; it
  does not call a paid AI API.
- GitHub Actions is a batch runner, not a web server. The job can take a while
  and is limited to six hours.
- Standard GitHub-hosted runner time is free for public repositories, but
  workflow logs and downloadable artifacts can be visible to anyone who can
  read a public repository. Private repositories have monthly Actions and
  artifact-storage allowances.
- YouTube may block downloads from GitHub-hosted runner IP addresses. This
  workflow does not use account cookies or try to bypass those blocks; a
  blocked or restricted source fails with an error.
- Automatic clip ranking is heuristic, not a guarantee of viral highlights.
  Review the clips before posting. Very short videos or videos with too little
  speech may not contain ten distinct usable segments.
- Only process videos you own or have permission to use, and follow the
  source platform's terms.

## Local checks

```bash
python -m unittest discover -s tools -p 'test_*.py'
```

To run the processor locally, install FFmpeg, install `tools/requirements.txt`,
then run `python tools/clipper.py --url '<YouTube URL>' --confirm-rights`.
