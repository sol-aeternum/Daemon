# Bundled speech model notices

Kokoro-82M v1.0 weights and voice assets: Apache License 2.0.
Source: https://huggingface.co/hexgrad/Kokoro-82M
ONNX export: https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1
kokoro-onnx runtime: MIT; installed distribution contains its license.
ONNX Runtime: MIT. Phonemizer: GPL-3.0-or-later.
PyAV: BSD-3-Clause, with bundled FFmpeg component notices
in the installed wheel. eSpeak NG is GPL-3.0-or-later; its bundled loader/runtime
distribution contains its license. Preserve these notices when distributing images.
Commercial use is permitted; runtime licensing remains separate from model licensing.
Image redistribution must satisfy the applicable GPL/source-availability obligations
for phonemizer/eSpeak and bundled-library license terms, not just the model's
Apache license. Sources: https://github.com/bootphon/phonemizer and
https://github.com/espeak-ng/espeak-ng; espeakng-loader source:
https://github.com/thewh1teagle/espeakng-loader. Review distribution obligations
before shipping a proprietary appliance/image to third parties.

Model author credits StyleTTS2 (Li et al.) and ISTFTNet. The upstream model card
attributes training audio from Koniwa tnc (CC BY 3.0) and SIWIS (CC BY 4.0):
https://github.com/koniwa/koniwa and https://datashare.ed.ac.uk/handle/10283/2353.
Daemon does not claim to reproduce or clone an ElevenLabs voice.

The complete model Apache-2.0 license is acquired during image build alongside
the model (fixed upstream source); see download_assets.py. No license/model
downloads occur at runtime.
