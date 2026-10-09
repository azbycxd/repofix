"""Final offline-only checks. Does not rerun Agent or Judge."""

from interview_eval import PYTHON, run_command


def main():
    commands = [
        ('pytest-final-offline', [PYTHON, '-m', 'pytest', '-q']),
        ('final-diff-check', ['git', 'diff', '--check']),
        ('final-docker-disk', ['docker', 'system', 'df']),
        ('final-experiment-containers', [
            'docker', 'ps', '-a', '--filter', 'name=interview',
            '--format', '{{.Names}}\t{{.Status}}',
        ]),
    ]
    for label, command in commands:
        result = run_command(label, command, timeout=300)
        if result['exit_code']:
            raise RuntimeError(f'{label} failed; preserved command logs')


if __name__ == '__main__':
    main()
