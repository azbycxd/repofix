"""Read-only baseline identity check using image-local Git objects."""

import json

from interview_eval import OUT, run_command, write_json
from repofix.env import DockerEnv


def main():
    rows = []
    for item in json.loads((OUT / 'task_images.json').read_text()):
        with DockerEnv(item['task_id'], item['image'], 'interview-identity-only') as env:
            base = item['base_commit']
            commands = [f'git rev-parse {base}', f'git rev-parse {base}^{{tree}}',
                        'git rev-parse HEAD', 'git rev-parse HEAD^{tree}',
                        f'git diff --name-status {base} HEAD --']
            output = []
            for command in commands:
                result = env.execute(command)
                output.append(dict(command=command,exit_code=result.exit_code,output=result.output))
            row = dict(task_id=item['task_id'],checks=output)
            rows.append(row)
            print(json.dumps(row), flush=True)
    write_json(OUT / 'image_local_baselines.json', rows)


if __name__ == '__main__':
    main()
