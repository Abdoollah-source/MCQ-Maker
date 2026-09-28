"""Generate a multi-size Windows icon from the MCQ Maker sheet motif."""
from pathlib import Path
import struct

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen


def render(size):
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    scale = size / 32
    color = QColor('#68445F')
    painter.setPen(QPen(color, max(1.2, 1.6 * scale)))
    painter.drawRoundedRect(6 * scale, 3 * scale, 20 * scale, 26 * scale, 3 * scale, 3 * scale)
    for y in (10, 16, 22):
        painter.setBrush(color if y == 16 else Qt.NoBrush)
        painter.drawEllipse(10 * scale, (y - 1) * scale, 3 * scale, 3 * scale)
        painter.drawLine(17 * scale, (y + 1) * scale, 22 * scale, (y + 1) * scale)
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, 'PNG')
    return bytes(data)


def main():
    sizes = (16, 20, 24, 32, 48, 64, 128, 256)
    images = [(size, render(size)) for size in sizes]
    offset = 6 + 16 * len(images)
    directory = [struct.pack('<HHH', 0, 1, len(images))]
    payload = []
    for size, data in images:
        dimension = 0 if size == 256 else size
        directory.append(struct.pack('<BBBBHHII', dimension, dimension, 0, 0, 1, 32, len(data), offset))
        payload.append(data)
        offset += len(data)
    target = Path(__file__).resolve().parents[1] / 'mcq_maker' / 'assets' / 'app_icon.ico'
    target.write_bytes(b''.join(directory + payload))


if __name__ == '__main__':
    main()
