---
name: youtube-video
description: Show a YouTube video with sound on Lampy's display, including the request to load up Celery Man, or stop playback and return to the tracking eyes.
---

# YouTube video

When the user asks to watch or see a YouTube video, search for the subject they requested and play the first result on the display with audio. Send the search text as `query` in a hardware marker at the start of your reply:

```text
[HW:/display/video/play:{"query":"sea otters"}]
```

When the user says “load up celery man” (sometimes transcribed as “load up celery, ma’am”), or asks to show or play a video without giving a topic, use this default video:

```text
[HW:/display/video/play:{"query":"https://www.youtube.com/watch?v=maAFcEU6atk"}]
```

The player temporarily takes over the eye display. It restores the tracking eyes when playback ends or stops. Do not speak a reply or send an emotion or eyes-mode marker alongside playback; that could interrupt the video. The player starts asynchronously, so do not claim a particular title was found or that playback succeeded merely because you emitted the marker.

When a video is active, requests like “stop”, “enough”, or “off” mean stop the video. Use `[HW:/display/video/stop:{}]` and let the display return to tracking eyes. Do not treat an explicit request to switch off a different device, such as a light, as a request to stop the video.

If the player reports it is unavailable, cannot find a result, or cannot play it, tell the user. Do not substitute audio-only music for a video request.
