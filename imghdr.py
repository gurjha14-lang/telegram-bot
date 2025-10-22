import io
import struct

def what(file, h=None):
    if h is None:
        if isinstance(file, (str, bytes)):
            with open(file, 'rb') as f:
                h = f.read(32)
        elif hasattr(file, 'read'):
            pos = file.tell()
            h = file.read(32)
            file.seek(pos)
        else:
            return None
    for typ, test in tests:
        res = test(h)
        if res:
            return res
    return None

def test_jpeg(h):
    if h[6:10] in (b'JFIF', b'Exif'):
        return 'jpeg'

def test_png(h):
    if h[:8] == b'\211PNG\r\n\032\n':
        return 'png'

def test_gif(h):
    if h[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'

tests = [("jpeg", test_jpeg), ("png", test_png), ("gif", test_gif)]
