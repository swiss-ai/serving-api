# Audio test fixtures

The original recording is "The quick brown fox jumps over the lazy dog", 3.8 seconds. The WAV and FLAC files keep only the word "quick", 0.32 seconds, to stay small.

| File | Audio | Format |
| :- | :- | :- |
| `fox_44k_stereo.ogg` | Full sentence | Original, Ogg Vorbis, 44.1 kHz stereo |
| `fox_44k_stereo.mp3` | Full sentence | MP3, 128 kbit/s, 44.1 kHz stereo |
| `quick_24k_mono.wav` | "quick" | 16-bit WAV, 24 kHz mono |
| `quick_16k_mono.wav` | "quick" | 16-bit WAV, 16 kHz mono |
| `quick_8k_mono.wav` | "quick" | 16-bit WAV, 8 kHz mono |
| `quick_8k_stereo.wav` | "quick" | 16-bit WAV, 8 kHz stereo |
| `quick_16k_mono.flac` | "quick" | FLAC, 16 kHz mono |

- **Source:** [Audio Sample - The Quick Brown Fox Jumps Over The Lazy Dog.ogg](https://commons.wikimedia.org/wiki/File:Audio_Sample_-_The_Quick_Brown_Fox_Jumps_Over_The_Lazy_Dog.ogg) on Wikimedia Commons
- **Author:** Russell Sacobie (RussmanJr)
- **Licence:** [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)
- **Changes:** the `.ogg` is unchanged. The other files were converted from it with ffmpeg, and the WAV and FLAC files were trimmed from 0.34 to 0.66 seconds.

This licence covers the audio files in this folder only.
