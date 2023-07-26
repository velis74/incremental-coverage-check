import re


class DiffParser:
    diff = ""

    def __init__(self, diff) -> None:
        self.diff = diff

        if diff is None or diff == "":
            raise Exception("Diff must be provided")

        if not self.validate_data():
            return None
            # raise Exception("Diff format is not correct")

    def validate_data(self) -> bool:
        return_value = False
        diff = self.diff.split("\n")

        if type(diff) is list and len(diff) > 2:
            return_value = True

        if diff[0][0:4] == "diff":
            return_value = True
        else:
            return_value = False

        return return_value

    def parse_header(self, diff_chunk) -> int:
        header_pattern = re.compile(r"@@ -(\d+),\d+ \+(\d+),\d+ @@")

        # Find the header information
        header_match = re.search(header_pattern, diff_chunk)

        if not header_match:
            raise ValueError("Invalid git diff chunk format")

        # Extract the starting line numbers from the header
        starting_line_in_old_file = int(header_match.group(1))
        starting_line_in_new_file = int(header_match.group(2))

        return starting_line_in_new_file

    def parse(self):
        changed_lines = []

        lines = self.diff.split("\n")
        file_line_nr = 0
        for line in lines:
            file_line_nr += 1
            if line[0:4] == "diff":
                pass
            elif line[0:5] == "index":
                pass
            elif line[0:3] == "---":
                pass
            elif line[0:3] == "+++":
                pass
            elif line[0:2] == "@@":
                file_line_nr = self.parse_header(line) - 1
            elif line[0:1] == " ":
                pass
            elif line[0:1] == "+":
                changed_lines.append(file_line_nr)
            elif line[0:1] == "-":
                file_line_nr -= 1

        return changed_lines
