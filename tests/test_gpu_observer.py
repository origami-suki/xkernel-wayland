"""Exercise observer shutdown against a local CDP pipe peer, without Chromium."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'tests/chromium/gpu-observer.c'
PEER = r'''
#define main observer_program_main
#define execl fake_execl
#define sleep fake_sleep
#include SOURCE
#undef main
#undef execl
#undef sleep
unsigned fake_sleep(unsigned n) { (void)n; usleep(10000); return 0; }
int fake_execl(const char *path, const char *arg, ...) {
    (void)path; (void)arg;
    char json[512]; size_t used = 0;
    for (;;) {
        char c;
        if (read(3, &c, 1) != 1) _exit(3);
        if (c) {
            if (used == sizeof(json) - 1) _exit(4);
            json[used++] = c;
            continue;
        }
        json[used] = 0; used = 0;
        int id = reply_id(json);
        if (id == 3) {
            const char *mode = getenv("OBSERVER_PEER_MODE");
            if (!strcmp(mode, "error"))
                send_command(4, "{\"id\":3,\"error\":{\"code\":-32000}}");
            else if (!strcmp(mode, "missing"))
                send_command(4, "{\"id\":3}");
            else if (strcmp(mode, "eof"))
                send_command(4, "{\"id\":3,\"result\":{}}");
            usleep(50000);
            _exit(!strcmp(mode, "nonzero") ? 7 : 0);
        }
        char reply[128];
        snprintf(reply, sizeof(reply), "{\"id\":%d,\"result\":{}}", id);
        if (send_command(4, reply)) _exit(5);
    }
}
int main(int argc, char **argv) {
    (void)argc;
    char *args[] = {argv[0], argv[1], "1", NULL};
    return observer_program_main(3, args);
}
'''


@unittest.skipUnless(shutil.which('gcc'), 'host C compiler required')
class ObserverShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='gpu-observer-test-')
        cls.binary = Path(cls.directory.name) / 'peer'
        code = PEER.replace('#include SOURCE', '#include ' + json.dumps(str(SOURCE)))
        subprocess.run(['gcc', '-Wall', '-Wextra', '-Werror', '-O2', '-x', 'c', '-',
                        '-o', str(cls.binary)], input=code, text=True, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def run_peer(self, mode, expected):
        result = subprocess.run([str(self.binary), str(Path(self.directory.name) / 'log')],
                                env={**os.environ, 'OBSERVER_PEER_MODE': mode},
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=10)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertEqual('CHROMIUM_GPU_OBSERVATION_DONE' in result.stdout, expected == 0)
        self.assertNotIn('CHROMIUM_GPU_FORCED_KILL', result.stdout)
        return result.stdout

    def test_close_error_with_child_zero_fails(self):
        output = self.run_peer('error', 1)
        self.assertIn('CHROMIUM_GPU_BROWSER_CLOSE_REPLY_FAILED', output)
        self.assertIn('normal_exit=1 code=0', output)

    def test_close_missing_result_with_child_zero_fails(self):
        self.assertIn('CHROMIUM_GPU_BROWSER_CLOSE_REPLY_FAILED', self.run_peer('missing', 1))

    def test_close_ack_and_child_zero_pass(self):
        self.run_peer('success', 0)

    def test_normal_close_eof_with_child_zero_pass(self):
        self.run_peer('eof', 0)

    def test_close_ack_does_not_hide_nonzero_child(self):
        self.assertIn('normal_exit=1 code=7', self.run_peer('nonzero', 1))
