"""pda.py — decodificador de audio .pda de Playdate.

Formato (cranksters/playdate-reverse-engineering/formats/pda.md):
  offset 0   : "Playdate AUD" (12 bytes)
  offset 12  : uint24 sample rate (Hz)
  offset 15  : uint8  formato
  offset 16  : datos de audio

Formatos:
  0 = 8-bit mono PCM (unsigned)    1 = 8-bit stereo PCM
  2 = 16-bit mono PCM (signed LE)  3 = 16-bit stereo PCM
  4 = 4-bit IMA ADPCM mono         5 = 4-bit IMA ADPCM stereo

Devuelve (rate, samples) donde samples es una lista de floats en [-1, 1]
(mono: se mezclan los canales).
"""

import struct

_IMA_STEP = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41,
    45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190,
    209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724,
    796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272,
    2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132,
    7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500,
    20350, 22385, 24623, 27086, 29794, 32767,
]
_IMA_INDEX = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8]


def _adpcm_decode(data, channels):
    """Decodifica IMA ADPCM. Devuelve lista de floats mono (mezclada)."""
    block_size = struct.unpack_from("<H", data, 0)[0]
    pos = 2
    # estado por canal
    pred = [0] * channels
    idx = [0] * channels
    out = []
    n = len(data)
    while pos < n:
        # cabecera de bloque: 4 bytes por canal
        for c in range(channels):
            if pos + 4 > n:
                return out
            pred[c] = struct.unpack_from("<h", data, pos)[0]
            idx[c] = data[pos + 2]
            pos += 4
        # nibbles: estéreo -> canal 0 = nibble alto, canal 1 = nibble bajo
        while pos < n and (pos - 2) % block_size != 0:
            byte = data[pos]
            pos += 1
            for c in range(channels):
                nib = (byte >> 4) if c == 0 else (byte & 0x0F)
                step = _IMA_STEP[idx[c]]
                diff = step >> 3
                if nib & 1:
                    diff += step >> 2
                if nib & 2:
                    diff += step >> 1
                if nib & 4:
                    diff += step
                if nib & 8:
                    diff = -diff
                pred[c] += diff
                if pred[c] > 32767:
                    pred[c] = 32767
                elif pred[c] < -32768:
                    pred[c] = -32768
                idx[c] += _IMA_INDEX[nib & 7]
                if idx[c] < 0:
                    idx[c] = 0
                elif idx[c] > 88:
                    idx[c] = 88
                out.append(pred[c] / 32768.0)
    return out


def decode_pda(data):
    """Decodifica un .pda. Devuelve (rate, samples_float_mono)."""
    if data[:12] != b"Playdate AUD":
        raise ValueError("no es un .pda valido")
    rate = int.from_bytes(data[12:15], "little")
    fmt = data[15]
    body = data[16:]

    if fmt == 0:      # 8-bit mono
        samples = [(b - 0x80) / 128.0 for b in body]
    elif fmt == 1:    # 8-bit stereo
        raw = [(b - 0x80) / 128.0 for b in body]
        samples = [(raw[i] + raw[i + 1]) / 2.0 for i in range(0, len(raw) - 1, 2)]
    elif fmt == 2:    # 16-bit mono
        n = len(body) // 2
        samples = [struct.unpack_from("<h", body, i * 2)[0] / 32768.0 for i in range(n)]
    elif fmt == 3:    # 16-bit stereo
        n = len(body) // 4
        samples = []
        for i in range(n):
            l = struct.unpack_from("<h", body, i * 4)[0] / 32768.0
            r = struct.unpack_from("<h", body, i * 4 + 2)[0] / 32768.0
            samples.append((l + r) / 2.0)
    elif fmt == 4:    # ADPCM mono
        samples = _adpcm_decode(body, 1)
    elif fmt == 5:    # ADPCM stereo
        samples = _adpcm_decode(body, 2)
    else:
        raise ValueError(f"formato .pda desconocido: {fmt}")
    return (rate, samples)
