"""Copy encoded H.264 into durable browser-playable HLS without frame sampling."""
import json
import os
import shutil
import subprocess


def ffmpeg_path():
    return os.environ.get('IOTDESIGN_FFMPEG') or shutil.which('ffmpeg')


class VideoPlayback:
    def __init__(self, source, rotation=0):
        self.folder = source.with_suffix('.playback').resolve()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.rotation = rotation
        self.error = None
        self.process = None
        self.log = None
        binary = ffmpeg_path()
        if not binary:
            self.error = 'FFmpeg is required for browser video playback; source video is preserved.'
        else:
            self.log = (self.folder / 'ffmpeg.log').open('wb')
            try:
                self.process = subprocess.Popen([
                    binary, '-hide_banner', '-loglevel', 'error', '-fflags', '+genpts',
                    '-probesize', '32', '-analyzeduration', '0', '-r', '30', '-f', 'h264', '-i', 'pipe:0', '-map', '0:v:0', '-c:v', 'copy',
                    '-an', '-f', 'hls', '-hls_time', '1', '-hls_list_size', '0',
                    '-hls_segment_type', 'fmp4', '-hls_fmp4_init_filename', 'init.mp4',
                    '-hls_segment_filename', str(self.folder / 'segment-%06d.m4s'),
                    '-hls_flags', 'independent_segments+temp_file', str(self.folder / 'index.m3u8')],
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self.log, cwd=self.folder,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except OSError as exc:
                self.error = f'Could not start video playback: {exc}'
        self.save_state(False)

    def save_state(self, complete):
        temporary = self.folder / 'state.tmp'
        temporary.write_text(json.dumps({'rotation': self.rotation, 'complete': complete,
                                         'error': self.error}), encoding='utf-8')
        temporary.replace(self.folder / 'state.json')

    def feed(self, chunk):
        if self.process is None or self.error:
            return
        try:
            self.process.stdin.write(chunk)
            self.process.stdin.flush()
        except (OSError, ValueError):
            self.error = 'Browser video remuxer stopped; encoded source remains preserved.'
            self.save_state(False)

    def close(self):
        if self.process:
            try:
                self.process.stdin.close()
                if self.process.wait(timeout=15):
                    self.error = 'Video playback remux failed; encoded source remains preserved.'
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait()
                self.error = 'Video playback remux did not finish.'
        if self.log:
            self.log.close()
        self.save_state(True)
