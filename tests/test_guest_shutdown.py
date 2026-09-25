"""The DONE marker is not an acknowledgement that the shell can read again."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_guest


class GuestShutdownTests(unittest.TestCase):
    def test_exit_waits_for_every_fragment_of_post_marker_prompt(self):
        # Reproduce the failed run's ordering: DONE is visible before the prompt.
        transcript = 'kylin-x:~# printf ...\n__XK_DONE_token__0\n'
        marker_end = transcript.index('__XK_DONE_token__0') + len('__XK_DONE_token__0')
        prompt = 'kylin-x:~# '
        serial = Mock()
        for size in range(len(prompt)):
            with self.subTest(prompt_bytes=size):
                self.assertFalse(run_guest.send_exit_after_prompt(
                    serial, transcript + prompt[:size], marker_end))
                serial.sendall.assert_not_called()
        self.assertTrue(run_guest.send_exit_after_prompt(serial, transcript + prompt, marker_end))
        serial.sendall.assert_called_once_with(b'exit "$xk_rc"\n')

    def test_old_prompt_before_done_does_not_authorize_exit(self):
        transcript = 'kylin-x:~# \n__XK_DONE_token__0\n'
        serial = Mock()
        self.assertFalse(run_guest.send_exit_after_prompt(serial, transcript, len(transcript) - 1))
        serial.sendall.assert_not_called()

    def test_complete_prompt_in_same_read_allows_exit(self):
        transcript = '__XK_DONE_token__17\nkylin-x:~# '
        serial = Mock()
        self.assertTrue(run_guest.send_exit_after_prompt(serial, transcript,
                                                       len('__XK_DONE_token__17')))
        # Preserve the recorded guest status; never change shutdown into exit 0.
        serial.sendall.assert_called_once_with(b'exit "$xk_rc"\n')


if __name__ == '__main__':
    unittest.main()
