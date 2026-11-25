"""Read the completion code configured for a new study deployment."""
import os


def assign_completion_code(session_id):
    return os.environ.get('PROLIFIC_COMPLETION_CODE', 'LOCALPREVIEW')
