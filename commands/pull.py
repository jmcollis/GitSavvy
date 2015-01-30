import sublime
from sublime_plugin import WindowCommand

from .base_command import BaseCommand


class GgPullCommand(WindowCommand, BaseCommand):

    def run(self):
        self.remotes = list(self.get_remotes().keys())
        self.remote_branches = self.get_remote_branches()

        if not self.remotes:
            self.window.show_quick_panel(["There are no remotes available."], None)
        else:
            self.window.show_quick_panel(self.remotes, self.on_select_remote, sublime.MONOSPACE_FONT)

    def on_select_remote(self, remote_index):
        # If the user pressed `esc` or otherwise cancelled.
        if remote_index == -1:
            return

        self.selected_remote = self.remotes[remote_index]
        selected_remote_prefix = self.selected_remote + "/"

        self.branches_on_selected_remote = [
            branch for branch in self.remote_branches
            if branch.startswith(selected_remote_prefix)
        ]

        current_local_branch = self.get_current_branch_name()

        try:
            print("looking for", selected_remote_prefix + current_local_branch)
            pre_selected_index = self.branches_on_selected_remote.index(
                selected_remote_prefix + current_local_branch)
            print("found at", pre_selected_index)
        except ValueError:
            pre_selected_index = None

        def deferred_panel():
            self.window.show_quick_panel(
                self.branches_on_selected_remote,
                self.on_select_branch,
                sublime.MONOSPACE_FONT,
                pre_selected_index
                # 16
            )

        sublime.set_timeout(deferred_panel)

    def on_select_branch(self, branch_index):
        # If the user pressed `esc` or otherwise cancelled.
        if branch_index == -1:
            return

        selected_branch = self.branches_on_selected_remote[branch_index].split("/", 1)[1]
        sublime.set_timeout_async(lambda: self.do_pull(self.selected_remote, selected_branch))

    def do_pull(self, remote, branch):
        sublime.status_message("Starting pull...")
        self.pull(remote, branch)
        sublime.status_message("Pull complete.")