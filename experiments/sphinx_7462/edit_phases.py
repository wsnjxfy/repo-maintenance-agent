"""Allocate a baseline run across localization, editing, and testing."""


class EditPhases:
    LOCATE_ACTIONS = 8
    LOCATE_REQUESTS = 12
    EDIT_READS = 2
    VERIFY_REQUESTS = 4
    VERIFY_ACTIONS = 2

    def __init__(self):
        self.phase = "locate"
        self.directed_reads = 0
        self.tested = False
        self.needs_test = False

    def plan(self, calls_used, actions_used, max_calls, max_actions, tools):
        reserve_reached = (max_calls - calls_used <= self.VERIFY_REQUESTS
                           or max_actions - actions_used <= self.VERIFY_ACTIONS)
        if reserve_reached or self.needs_test:
            self.phase = "verify"
        elif (self.phase != "locate" or calls_used >= self.LOCATE_REQUESTS
              or actions_used >= self.LOCATE_ACTIONS):
            self.phase = "edit"

        if self.phase == "locate":
            allowed = {"list_files", "search_text", "read_lines", "finish"}
            message = "Locate the relevant code. The controller will switch to editing when the localization budget is reached."
        elif self.phase == "edit":
            allowed = {"replace_text", "run_visible_tests", "finish"}
            reads_left = self.EDIT_READS - self.directed_reads
            if reads_left:
                allowed.add("read_lines")
            message = (
                "Use replace_text for a small change supported by the source. "
                f"You have {reads_left} targeted reads left. For a read, name one file and give a start and end line "
                "covering at most 200 lines. If you cannot identify a justified change, finish with the blocker."
            )
        else:
            allowed = {"run_visible_tests"} if self.needs_test else {"run_visible_tests", "finish"}
            message = (
                "Run a focused visible test for the latest edit." if self.needs_test else
                "Use the remaining budget for focused visible tests or finish with the current result."
            )
        return self.phase, {name: description for name, description in tools.items() if name in allowed}, message

    def observe(self, tool, response):
        if self.phase == "edit" and tool == "read_lines":
            self.directed_reads += 1
        if response["status"] == "ok":
            if tool == "replace_text":
                self.needs_test = True
                self.tested = False
            elif tool == "run_visible_tests":
                self.needs_test = False
                self.tested = True
