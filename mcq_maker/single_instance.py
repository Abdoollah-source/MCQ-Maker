"""One running MCQ Maker window per Windows user."""
from hashlib import sha256
import logging
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def default_server_name():
    user_key = sha256(str(Path.home()).casefold().encode('utf-8')).hexdigest()[:12]
    return f'MCQ-Maker-{user_key}'


class SingleInstance(QObject):
    activation_requested = Signal()

    def __init__(self, name=None, parent=None):
        super().__init__(parent)
        self.name = name or default_server_name()
        self.server = None
        self._connections = []

    def acquire(self):
        logger = logging.getLogger('mcq_maker.single_instance')
        probe = QLocalSocket(self)
        probe.connectToServer(self.name)
        if probe.waitForConnected(250):
            logger.info('Existing application instance found')
            probe.write(b'open')
            probe.waitForBytesWritten(250)
            probe.disconnectFromServer()
            return False

        QLocalServer.removeServer(self.name)
        server = QLocalServer(self)
        if not server.listen(self.name):
            logger.error('Single-instance endpoint could not start: %s', server.errorString())
            return True
        logger.info('Single-instance endpoint started')
        self.server = server
        server.newConnection.connect(self._accept_connection)
        return True

    def _accept_connection(self):
        while self.server and self.server.hasPendingConnections():
            connection = self.server.nextPendingConnection()
            self._connections.append(connection)
            connection.disconnected.connect(lambda c=connection: self._forget(c))
            connection.disconnectFromServer()
            self.activation_requested.emit()

    def _forget(self, connection):
        if connection in self._connections:
            self._connections.remove(connection)
        connection.deleteLater()

    def close(self):
        if self.server is not None:
            self.server.close()
            QLocalServer.removeServer(self.name)
            self.server = None
