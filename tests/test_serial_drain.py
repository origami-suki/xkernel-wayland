"""Host transport regression: monitor progress must not need controller serial reads."""
import socket
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from serial_drain import SerialDrain


class SerialDrainTests(unittest.TestCase):
    def test_large_serial_output_does_not_block_monitor_progress(self):
        serial, producer = socket.socketpair()
        monitor, responder = socket.socketpair()
        serial.settimeout(0.1)
        monitor.settimeout(5)
        producer.settimeout(5)
        responder.settimeout(5)
        producer.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        payload = b'x' * (1024 * 1024)
        errors = []

        def emulate_qemu():
            try:
                producer.sendall(payload)
                producer.shutdown(socket.SHUT_WR)
                self.assertEqual(responder.recv(1), b'?')
                responder.sendall(b'!')
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=emulate_qemu)
        drain = SerialDrain(serial)
        try:
            worker.start()
            monitor.sendall(b'?')
            # No controller recv(serial): the simulated monitor must still reply.
            self.assertEqual(monitor.recv(1), b'!')
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            drain.check()
        finally:
            result = drain.stop()
            for channel in (serial, producer, monitor, responder):
                channel.close()
            worker.join(timeout=5)
        # The producer can leave its final socket-buffer bytes pending at stop;
        # it cannot reach the monitor unless most of this payload was consumed.
        self.assertGreater(result['bytes_received'], len(payload) // 2)
        self.assertIsNone(result['error'])

    def test_rejects_unbounded_socket(self):
        serial, producer = socket.socketpair()
        try:
            with self.assertRaises(ValueError):
                SerialDrain(serial)
        finally:
            serial.close()
            producer.close()


if __name__ == '__main__':
    unittest.main()
