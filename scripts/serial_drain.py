"""Keep QEMU's serial socket writable while the controller waits on its monitor.

QEMU's chardev logfile remains authoritative. This consumer only prevents unread
socket output from applying backpressure to QEMU during synchronous captures.
The caller must set a bounded socket timeout and stop the consumer before close.
"""
import socket
import threading


class SerialDrain:
    def __init__(self, client):
        if client.gettimeout() is None or client.gettimeout() <= 0:
            raise ValueError('serial socket must have a positive bounded timeout')
        self.client = client
        self.bytes_received = 0
        self.eof = False
        self.error = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name='qemu-serial-drain', daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                data = self.client.recv(65536)
            except socket.timeout:
                continue
            except OSError as error:
                self.error = str(error)
                return
            if not data:
                self.eof = True
                return
            self.bytes_received += len(data)

    def check(self):
        if self.error is not None:
            raise OSError('serial drain failed: ' + self.error)

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=self.client.gettimeout() + 1)
        if self._thread.is_alive():
            raise RuntimeError('serial drain did not stop')
        return {'bytes_received': self.bytes_received, 'eof': self.eof, 'error': self.error}
