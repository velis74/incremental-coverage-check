import json
import os.path

import configargparse
from configargparse import RawTextHelpFormatter
import subprocess
from diff_parser import DiffParser

import logging
import logging.handlers


def parse_args():
    default_config_files = []
    parser = configargparse.ArgParser(
        description="Incremental coverage check",
        formatter_class=RawTextHelpFormatter,
        default_config_files=default_config_files,
    )
    parser.add_argument("-l", "--logging-level", type=str, default="INFO", help="Logging level INFO/DEBUG")
    parser.add_argument("-f", "--files", type=str, nargs="+", default=None, help="Files")
    parser.add_argument("-j", "--coverage-json", type=str, required=True, help="Boot path")
    parser.add_argument("-p", "--required-percentage", type=int, default=70, help="Required percentage")
    parser.add_argument("-b", "--branch", type=str, required=True, help="PR Branch")
    parser.add_argument("-c", "--current-branch", type=str, default=None, required=False, help="Current Branch")
    parser.add_argument("-w", "--working-dir", type=str, required=True, help="Working dir")

    args, unknown = parser.parse_known_args()
    return args


def parse_coverage_file(args):
    logging.debug("Start parsing coverage file.")
    coverage_data = {}
    with open(args.coverage_json) as coverage_json_file:
        coverage_data = json.load(coverage_json_file)
        logging.debug(f"{len(coverage_data)} files in coverage.json file.")
    return coverage_data


def get_file_diff(curr_branch, branch, path, file):
    try:
        result = subprocess.check_output(["git", "-C", path, "diff", f"{branch}..{curr_branch}", "--", file])
        files_list = result.decode("utf-8").strip()
        return files_list
    except subprocess.CalledProcessError as e:
        logging.debug(e)
        return False


def get_changed_files(curr_branch, branch, path):
    logging.debug("Get changed files from diff.")
    try:
        result = subprocess.check_output(
            [
                "git",
                "-C",
                path,
                "diff",
                "--name-only",
                # f"{branch}..{curr_branch}",
                f"{branch}..HEAD",
            ]
        )
        files_list = result.decode("utf-8").strip().split("\n")
        logging.debug(f"{len(files_list)} files changed in diff.")
        return files_list
    except subprocess.CalledProcessError as e:
        logging.debug(e)
        return False


def get_curr_branch(path):
    try:
        result = subprocess.check_output(["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"])
        current_branch = result.decode("utf-8").strip()
        return current_branch
    except subprocess.CalledProcessError:
        return False


def main():
    try:
        success = True
        args = parse_args()

        logging.basicConfig(level=getattr(logging, args.logging_level))

        coverage_data = parse_coverage_file(args)

        if not args.current_branch:
            args.current_branch = get_curr_branch(args.working_dir)

        if args.files == None:
            args.files = get_changed_files(args.current_branch, args.branch, args.working_dir)

        for file in args.files:
            logging.debug(f"Working on file: {file}")
            file_data = coverage_data.get(os.path.join(args.working_dir, file), None)
            if file_data:
                diff = get_file_diff(
                    args.current_branch, args.branch, args.working_dir, os.path.join(args.working_dir, file)
                )
                parser = DiffParser(diff)
                a = parser.parse()
                s = file_data["s"]
                count = 0
                percentage = 0
                for line_nr, value in s.items():
                    if int(line_nr) + 1 in a and value > 0:
                        count += 1

                percentage = round((count / len(a)) * 100)

                print(f"{file} Lines: {len(a)} score: {count}, percentage: {percentage}")

                if percentage < args.required_percentage:
                    success = False

        if not success:
            raise SystemExit("Failed")

    except Exception as e:
        raise SystemExit(e)


if __name__ == "__main__":
    main()
