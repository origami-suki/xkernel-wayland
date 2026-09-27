"""Host-only graphics selection tests: no real render node or QEMU is used."""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import qemu_graphics
import run_guest
import run_linux_guest


class GraphicsOptionsTests(unittest.TestCase):
    def node(self, resolved='/dev/dri/renderD128', mode=stat.S_IFCHR | 0o660):
        node = Mock()
        node.resolve.return_value = node
        node.stat.return_value.st_mode = mode
        node.__str__ = Mock(return_value=resolved)
        return node

    def test_default_2d(self):
        self.assertEqual(qemu_graphics.graphics_options('2d'),
                         ['-device', 'virtio-gpu-pci', '-display', 'none'])

    def test_unsupported_backend_and_2d_node(self):
        for backend, node in [('invalid', None), ('2d', Path('/dev/dri/renderD128')),
                              ('virgl', None)]:
            with self.subTest(backend=backend, node=node), self.assertRaises(ValueError):
                qemu_graphics.graphics_options(backend, node)

    def test_missing_node(self):
        with patch.object(qemu_graphics, 'Path') as factory:
            factory.return_value.resolve.side_effect = FileNotFoundError('missing')
            with self.assertRaisesRegex(ValueError, 'cannot access render node'):
                qemu_graphics.graphics_options('virgl', '/missing')

    def test_reject_regular_file_and_nonrender_character_node(self):
        for path, mode in [('/dev/dri/renderD128', stat.S_IFREG | 0o660),
                           ('/dev/null', stat.S_IFCHR | 0o666),
                           ('/dev/dri/card0', stat.S_IFCHR | 0o660)]:
            with self.subTest(path=path, mode=mode), \
                    patch.object(qemu_graphics, 'Path', return_value=self.node(path, mode)), \
                    self.assertRaises(ValueError):
                qemu_graphics.graphics_options('virgl', path)

    def test_reject_no_readwrite_access(self):
        with patch.object(qemu_graphics, 'Path', return_value=self.node()), \
                patch.object(qemu_graphics.os, 'access', return_value=False), \
                self.assertRaisesRegex(ValueError, 'readable and writable'):
            qemu_graphics.graphics_options('virgl', '/dev/dri/renderD128')

    def test_by_path_resolves_and_records_original_node(self):
        requested = Path('/dev/dri/by-path/pci-example-render')
        node = self.node()
        with patch.object(qemu_graphics, 'Path', return_value=node), \
                patch.object(qemu_graphics.os, 'access', return_value=True) as access:
            options = qemu_graphics.graphics_options('virgl', requested)
        node.resolve.assert_called_once_with(strict=True)
        access.assert_called_once_with(node, os.R_OK | os.W_OK)
        self.assertEqual(options, ['-device', 'virtio-gpu-gl-pci', '-display',
                                  'egl-headless,rendernode=/dev/dri/renderD128'])
        metadata = qemu_graphics.graphics_metadata('virgl', requested, options)
        self.assertEqual(metadata['render_node_requested'], str(requested))
        self.assertEqual(metadata['render_node_resolved'], '/dev/dri/renderD128')
        self.assertFalse(metadata['guest_acceleration_verified'])


class RunnerGraphicsTests(unittest.TestCase):
    def test_invalid_cli_fails_before_disk_or_qemu(self):
        for runner in (run_guest, run_linux_guest):
            for arguments, exception in [(['--gpu-backend', 'invalid'], SystemExit),
                                         (['--gpu-backend', 'virgl'], ValueError),
                                         (['--render-node', '/dev/null'], ValueError)]:
                with self.subTest(runner=runner.__name__, arguments=arguments), \
                        patch.object(sys, 'argv', [runner.__file__, *arguments]), \
                        patch.object(runner.subprocess, 'Popen') as launch, \
                        contextlib.redirect_stderr(io.StringIO()), \
                        self.assertRaises(exception):
                    runner.main()
                launch.assert_not_called()

    def test_planned_launch_preserves_2d_order_and_selects_virgl(self):
        for runner in (run_guest, run_linux_guest):
            for backend in ('2d', 'virgl'):
                with self.subTest(runner=runner.__name__, backend=backend), \
                        tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
                    root = Path(temporary)
                    disk = root / 'work/images/test.img'
                    disk.parent.mkdir(parents=True)
                    disk.write_bytes(b'test')
                    arguments = ['--run-id', 'graphics-test', '--disk', str(disk)]
                    options = ['-device', 'virtio-gpu-pci', '-display', 'none']
                    if backend == 'virgl':
                        arguments += ['--gpu-backend', 'virgl', '--render-node',
                                      '/dev/dri/by-path/test-render']
                        options = ['-device', 'virtio-gpu-gl-pci', '-display',
                                   'egl-headless,rendernode=/dev/dri/renderD128']
                        stack.enter_context(patch.object(runner, 'graphics_options',
                                                         return_value=options))
                    stack.enter_context(patch.object(sys, 'argv', [runner.__file__, *arguments]))
                    stack.enter_context(patch.object(runner, 'ROOT', root))
                    stack.enter_context(patch.object(runner, 'git_state', return_value={}))
                    stack.enter_context(patch.object(runner.platform, 'uname', return_value=('test',)))
                    stack.enter_context(patch.object(runner, 'command', return_value='QEMU version 11.1.1'))
                    stack.enter_context(patch.object(runner, 'sha256', return_value='test-sha256'))
                    stack.enter_context(patch.object(runner, 'write_inventory', return_value={}))
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
                    if runner is run_guest:
                        stack.enter_context(patch.object(runner, 'check_bundle',
                                                         return_value=(root / 'kernel.bin', 'build-id')))
                    else:
                        stack.enter_context(patch.object(runner, 'prepare_boot', return_value={}))
                    launch = stack.enter_context(patch.object(runner.subprocess, 'Popen',
                                                              side_effect=RuntimeError('mock launch stop')))
                    self.assertEqual(runner.main(), 1)
                    argv = launch.call_args.args[0]
                    output = root / 'artifacts/runs/graphics-test'
                    state = json.loads((output / 'metadata.json').read_text())
                    socket_dir = Path(argv[argv.index('-monitor') + 1].split(',')[0][5:]).parent
                    expected = [runner.QEMU, '-machine', 'virt,gic-version=3', '-cpu', 'cortex-a76',
                                '-accel', 'tcg,thread=multi', '-m', '2g', '-smp', '4', '-kernel']
                    if runner is run_guest:
                        expected += [str(root / 'kernel.bin'), '-snapshot']
                    else:
                        expected += [str(output / 'linux/Image'), '-initrd',
                                     str(output / 'linux/initramfs-diagnostic.gz'), '-append',
                                     'console=ttyAMA0 root=/dev/vda rw', '-snapshot']
                    expected += ['-drive', f'id=disk0,if=none,format=raw,file={disk}',
                                 '-device', 'virtio-blk-pci,drive=disk0', '-object', 'rng-random,id=host_rng0',
                                 '-device', 'virtio-rng-pci,rng=host_rng0', *options[:2],
                                 '-device', 'virtio-keyboard-pci', '-device', 'virtio-mouse-pci',
                                 '-nic', 'none', '-vga', 'none', *options[2:], '-chardev',
                                 f'socket,id=serial0,path={socket_dir}/serial,server=on,wait=off,'
                                 f'logfile={output}/serial.log', '-serial', 'chardev:serial0', '-monitor',
                                 f'unix:{socket_dir}/monitor,server=on,wait=off', '-no-reboot']
                    self.assertEqual(argv, expected)
                    self.assertEqual(state['argv'], argv)
                    self.assertEqual(state['graphics']['backend'], backend)
                    self.assertFalse(state['graphics']['guest_acceleration_verified'])
                    self.assertEqual(state['qemu_graphics_sha256'], 'test-sha256')
                    self.assertTrue((output / 'qemu_graphics.py').is_file())


if __name__ == '__main__':
    unittest.main()
