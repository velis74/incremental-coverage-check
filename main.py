import json
import logging
import logging.handlers
import os.path
import subprocess

import configargparse

from configargparse import RawTextHelpFormatter
from github import Auth, Github

from diff_parser import DiffParser


def parse_args() -> configargparse.ArgParser:
    default_config_files = []
    parser = configargparse.ArgParser(
        description="Incremental coverage check",
        formatter_class=RawTextHelpFormatter,
        default_config_files=default_config_files,
    )
    parser.add_argument(
        "-l",
        "--logging-level",
        type=str,
        default="INFO",
        help="Logging level INFO/DEBUG",
    )
    parser.add_argument("-f", "--files", type=str, nargs="+", default=None, help="Files")
    parser.add_argument(
        "--clover-coverage-json",
        type=str,
        default="none",
        help="Clover coverage json file.",
    )
    parser.add_argument(
        "--py-coverage-json",
        type=str,
        default="none",
        help="Python coverage json file.",
    )
    parser.add_argument("-p", "--required-percentage", type=int, default=70, help="Required percentage")
    parser.add_argument("-b", "--branch", type=str, required=True, help="PR Branch")
    parser.add_argument(
        "-c",
        "--current-branch",
        type=str,
        default=None,
        required=False,
        help="Current Branch",
    )
    parser.add_argument("-w", "--working-dir", type=str, required=True, help="Working dir")
    parser.add_argument("-g", "--gh-token", type=str, default="none", help="Github token")
    parser.add_argument("-r", "--repository", type=str, default="none", help="Repository")
    parser.add_argument("-i", "--issue", type=str, default="none", help="Issue nr")

    args, unknown = parser.parse_known_args()
    return args


def parse_coverage_file(coverage_json) -> dict:
    logging.debug(f"Start parsing coverage file. {coverage_json}")
    coverage_data = {}
    with open(coverage_json) as coverage_json_file:
        data = json.load(coverage_json_file)
        for file, file_data in data.items():
            missing_lines = []
            for line, status in file_data["s"].items():
                if status == 0:
                    missing_lines.append(int(line) + 1)
            coverage_data.update({file: {"missing_lines": missing_lines}})
    return coverage_data


def parse_py_coverage_data(path, working_dir) -> dict:
    """
    file
    s:
        line:0/1
    """
    logging.debug(f"Start parsing Python coverage file. {path}")
    try:
        coverage_data = {}
        with open(path) as f:
            data = json.load(f)

            for file_name, file_data in data["files"].items():
                coverage_data.update(
                    {os.path.join(working_dir, file_name): {"missing_lines": file_data["missing_lines"]}}
                )
    except Exception as e:
        logging.debug(e)
    return coverage_data


def get_file_diff(curr_branch, branch, path, file) -> str:
    try:
        result = subprocess.check_output(["git", "-C", path, "diff", f"{branch}..{curr_branch}", "--", file])
        files_list = result.decode("utf-8").strip()
        return files_list
    except subprocess.CalledProcessError as e:
        logging.debug(e)
        return False


def get_changed_files(curr_branch, branch, path) -> list:
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
        return []


def get_curr_branch(path) -> str:
    try:
        result = subprocess.check_output(["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"])
        current_branch = result.decode("utf-8").strip()
        return current_branch
    except subprocess.CalledProcessError:
        return False


def intersection(a, b) -> dict:
    a_set = set(a)
    b_set = set(b)

    if a_set & b_set:
        return a_set & b_set
    return {}


def report2txt(report):
    logging.debug(f"Report: {report}")
    out = "Coverage report:\n\n"

    out += f"Total changed lines: {report['total_changed_lines']['count']}\n"
    out += "Checked files (only uncovered lines listed):\n"

    try:
        for file, data in report["checked_files"]["files"].items():
            # report.update({"checked_files": {"files": {file: {"uncovered_lines": coverage_intersection}}}})
            txt_uncovered_lines = collect_uncovered_lines_2_txt(data["uncovered_lines"])

            if txt_uncovered_lines == "0":
                txt_uncovered_lines = ""
            out += f"{file} ({data['covered']}%): {txt_uncovered_lines}\n"
    except TypeError as e:
        logging.debug(f"TypeError {e}")
    except Exception as e:
        logging.debug(f"Exception {e}")

    return out


def collect_uncovered_lines_2_txt(data):
    out = ""
    start = 0
    running = 0
    for line in data:
        if start == 0:
            start = running

        if line > running + 1:
            if out != "":
                out += ", "
            if start == 0:
                pass
            elif start == running:
                out += f"{running}"
            else:
                out += f"{start}-{running}"
            start = 0

        running = line

    if start == 0:
        start = running

    if out != "":
        out += ", "
    if start == running:
        out += f"{running}"
    else:
        out += f"{start}-{running}"
    return out


def get_all_lines_from_file(file_path):
    _uncovered_lines = []
    try:
        with open(file_path, "r") as fp:
            lines = fp.readlines()
            for line in range(len(lines)):
                _uncovered_lines.append(line + 1)
    except Exception as e:
        logging.debug(f"Cannot open file {e}")
        _uncovered_lines = [1]
    return _uncovered_lines


def is_ignored(file, path=None) -> bool:
    ignored_suffixes = ["md", "pyc", "pyo", "txt", "json", "gitignore", "gitattributes", "gitmodules", "yml"]
    ignored_prefixes = [".", "test_", ".github"]
    ignored_files = [
        "LICENSE",
        "README.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "PULL_REQUEST_TEMPLATE.md",
        "setup.py",
        "settings.py",
    ]
    ignored_folders = ["/migrations/"]

    _file = file.split("/")[-1]

    if _file in ignored_files:
        return True

    for suffix in ignored_suffixes:
        if file.endswith(suffix):
            return True

    for prefix in ignored_prefixes:
        if _file.startswith(prefix):
            return True

    if path is not None:
        for folder in ignored_folders:
            if os.path.join(path, file).count(folder) > 0:
                return True

    return False


def main() -> bool:
    try:
        args = parse_args()
        coverage_data = {}
        total_changed_lines = 0
        total_uncovered_lines = 0
        percentage = 0
        checked_files_count = 0
        skipped_files_count = 0
        report = dict()
        report_files = dict()

        logging.basicConfig(level=getattr(logging, args.logging_level))

        if not args.current_branch:
            args.current_branch = get_curr_branch(args.working_dir)

        logging.debug("Args files")
        if args.files is None:
            args.files = get_changed_files(args.current_branch, args.branch, args.working_dir)

        logging.debug("Args clover coverage json")
        if args.clover_coverage_json != "none":
            coverage_data = parse_coverage_file(os.path.join(args.working_dir, args.clover_coverage_json))

        logging.debug(f"Args py coverage json. {args.py_coverage_json}")
        if args.py_coverage_json != "none":
            coverage_data.update(
                parse_py_coverage_data(os.path.join(args.working_dir, args.py_coverage_json), args.working_dir)
            )

        for file in args.files:
            file_path = os.path.join(args.working_dir, file)
            logging.debug(f"Working on file: {file_path}")

            file_data = coverage_data.get(file_path, None)

            if file_data is None:
                logging.debug("File is not in coverage. ")
                if is_ignored(file, args.working_dir):
                    logging.debug("File is ignored. ")
                    skipped_files_count += 1
                else:
                    logging.debug("File is not ignored. Marking as uncovered.")

                    lines_from_file = get_all_lines_from_file(file_path)
                    report_files.update({file: {"uncovered_lines": lines_from_file, "covered": 1}})
                    total_changed_lines += len(lines_from_file)
                    total_uncovered_lines += len(lines_from_file)
                    checked_files_count += 1
            else:
                checked_files_count += 1
                logging.debug("Getting file diff")
                diff = get_file_diff(
                    args.current_branch,
                    args.branch,
                    args.working_dir,
                    os.path.join(args.working_dir, file),
                )
                parser = DiffParser(diff)
                changed_lines = parser.parse()
                logging.debug(f"Changed lines {len(changed_lines)}")

                logging.debug(f"Intersection {changed_lines}, {coverage_data[file_path]['missing_lines']}")
                coverage_intersection = intersection(changed_lines, coverage_data[file_path]["missing_lines"])

                if len(coverage_intersection) == 0:
                    logging.debug(f"All lines covered. Coverage intersection: {sorted(coverage_intersection)}")
                    total_changed_lines += len(changed_lines)
                    try:
                        file_percentage = round(
                            ((len(changed_lines) - len(coverage_intersection)) / len(changed_lines)) * 100
                        )
                    except ZeroDivisionError:
                        file_percentage = 100
                    report_files.update(
                        {file: {"uncovered_lines": sorted(coverage_intersection), "covered": file_percentage}}
                    )

                else:
                    logging.debug(f"Coverage intersection: {sorted(coverage_intersection)}")

                    total_changed_lines += len(changed_lines)
                    logging.debug(f"Total changed lines {total_changed_lines}")
                    total_uncovered_lines += len(coverage_intersection)
                    logging.debug(f"Total uncovered lines {total_uncovered_lines}")

                    try:
                        file_percentage = round(
                            ((len(changed_lines) - len(coverage_intersection)) / len(changed_lines)) * 100
                        )
                    except ZeroDivisionError:
                        file_percentage = 1

                    report_files.update(
                        {file: {"uncovered_lines": sorted(coverage_intersection), "covered": file_percentage}}
                    )

        report.update({"checked_files": {"count": checked_files_count, "files": report_files}})
        report.update({"skipped_files": {"count": skipped_files_count}})
        report.update({"total_changed_lines": {"count": total_changed_lines}})

        try:
            if total_uncovered_lines > 0 and total_changed_lines > 0 and total_uncovered_lines < total_changed_lines:
                percentage = round(((total_changed_lines - total_uncovered_lines) / total_changed_lines) * 100)
        except ZeroDivisionError:
            percentage = 1

        if total_uncovered_lines == 0:
            percentage = 100

        if total_uncovered_lines == 0:
            percentage = 100

        if checked_files_count > 0:
            logging.info(f"Total covered in changed lines: {percentage}%")
            logging.info(report2txt(report))
        else:
            logging.info("No files checked.")
            return True

        if args.gh_token != "none" and args.issue != "none":
            auth = Auth.Token(args.gh_token)
            g = Github(auth=auth)
            repo = g.get_repo(args.repository)
            pr = repo.get_issue(int(args.issue))
            comment = pr.create_comment(report2txt(report))
            logging.debug(f"Comment created: {comment}")

        if percentage < args.required_percentage and checked_files_count > 0:
            logging.info(f"Commit is not covered at least {args.required_percentage}%. Coverage FAILED.")
            raise SystemExit("Failed")

    except Exception as e:
        raise SystemExit(e)


if __name__ == "__main__":
    main()
