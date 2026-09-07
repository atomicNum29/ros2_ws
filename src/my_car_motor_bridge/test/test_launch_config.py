import importlib.util
from pathlib import Path

from launch import LaunchContext
from launch.actions import DeclareLaunchArgument
from launch.utilities import perform_substitutions


def test_yaml_defaults_and_explicit_mode_override(tmp_path, monkeypatch):
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'motor_bridge.yaml').write_text(
        'motor_bridge_node:\n  ros__parameters:\n    serial_mode: bridge\n'
        '    legacy_fallback_enabled: false\n    legacy_fallback_timeout_sec: 2.5\n'
    )
    path = Path(__file__).resolve().parents[1] / 'launch' / 'motor_bridge.launch.py'
    spec = importlib.util.spec_from_file_location('motor_bridge_launch_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'get_package_share_directory', lambda name: str(tmp_path))
    description = module.generate_launch_description()
    arguments = [entity for entity in description.entities if isinstance(entity, DeclareLaunchArgument)]
    context = LaunchContext()
    defaults = {argument.name: perform_substitutions(context, argument.default_value)
                for argument in arguments}
    assert defaults == {'serial_mode': 'bridge', 'legacy_fallback_enabled': 'false',
                        'legacy_fallback_timeout_sec': '2.5'}
    context.launch_configurations['serial_mode'] = 'control'
    for argument in arguments:
        argument.execute(context)
    assert context.launch_configurations['serial_mode'] == 'control'
