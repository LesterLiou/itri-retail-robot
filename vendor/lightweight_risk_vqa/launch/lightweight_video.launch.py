"""Launch lightweight video perception with the existing VQA/policy/UI."""
from lightweight_risk_vqa.launching import make_launch


def generate_launch_description():
    return make_launch('video')
