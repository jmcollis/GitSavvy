from functools import lru_cache, partial
import re
import threading

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from . import log_graph_colorizer as colorizer
from .log import GsLogActionCommand, GsLogCommand
from .navigate import GsNavigate
from ..git_command import GitCommand
from ..settings import GitSavvySettings
from ..ui_mixins.quick_panel import show_branch_panel
from ...common import util
from ...common.theme_generator import XMLThemeGenerator, JSONThemeGenerator


MYPY = False
if MYPY:
    from typing import Iterator, Set, Tuple


COMMIT_NODE_CHAR = "●"
COMMIT_NODE_CHAR_OPTIONS = "●*"
GRAPH_CHAR_OPTIONS = r" /_\|\-\\."
COMMIT_LINE = re.compile(
    "^[{graph_chars}]*[{node_chars}][{graph_chars}]* (?P<commit_hash>[a-f0-9]{{5,40}})".format(
        graph_chars=GRAPH_CHAR_OPTIONS, node_chars=COMMIT_NODE_CHAR_OPTIONS))
DOT_SCOPE = 'git_savvy.graph.dot'
PATH_SCOPE = 'git_savvy.graph.path_char'


class LogGraphMixin(object):

    """
    Open a new window displaying an ASCII-graphic representation
    of the repo's branch relationships.
    """

    def run(self, file_path=None, title=None):
        self._file_path = file_path
        self.title = title or "GRAPH"
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        # need to get repo_path before the new view is created.
        repo_path = self.repo_path

        view = util.view.get_scratch_view(self, "log_graph", read_only=True)
        view.set_syntax_file("Packages/GitSavvy/syntax/graph.sublime-syntax")
        view.run_command("gs_handle_vintageous")
        view.run_command("gs_handle_arrow_keys")
        threading.Thread(target=partial(augment_color_scheme, view)).run()

        settings = view.settings()
        settings.set("git_savvy.repo_path", repo_path)
        settings.set("git_savvy.file_path", self._file_path)
        self.prepare_target_view(view)
        view.set_name(self.title)

        view.run_command("gs_log_graph_refresh", {"navigate_after_draw": True})

    def prepare_target_view(self, view):
        pass


def augment_color_scheme(view):
    # type: (sublime.View) -> None
    settings = GitSavvySettings()
    colors = settings.get('colors').get('log_graph')
    if not colors:
        return

    color_scheme = view.settings().get('color_scheme')
    if color_scheme.endswith(".tmTheme"):
        themeGenerator = XMLThemeGenerator(color_scheme)
    else:
        themeGenerator = JSONThemeGenerator(color_scheme)
    themeGenerator.add_scoped_style(
        "GitSavvy Highlighted Commit Dot",
        DOT_SCOPE,
        background=colors['commit_dot_background'],
        foreground=colors['commit_dot_foreground'],
    )
    themeGenerator.add_scoped_style(
        "GitSavvy Highlighted Path Char",
        PATH_SCOPE,
        background=colors['path_background'],
        foreground=colors['path_foreground'],
    )
    themeGenerator.apply_new_theme("log_graph_view", view)


class GsLogGraphRefreshCommand(TextCommand, GitCommand):

    """
    Refresh the current graph view with the latest commits.
    """

    def run(self, edit, navigate_after_draw=False):
        sublime.set_timeout_async(partial(self.run_async, navigate_after_draw))

    def run_async(self, navigate_after_draw=False):
        file_path = self.file_path
        if file_path:
            graph_content = "File: {}\n\n".format(file_path)
        else:
            graph_content = ""

        args = self.build_git_command()
        graph_content += self.git(*args)
        graph_content = re.sub(
            r'(^[{}]*)\*'.format(GRAPH_CHAR_OPTIONS),
            r'\1' + COMMIT_NODE_CHAR, graph_content,
            flags=re.MULTILINE)

        self.view.run_command("gs_replace_view_text", {"text": graph_content, "restore_cursors": True})
        if navigate_after_draw:
            self.view.run_command("gs_log_graph_navigate")

        draw_info_panel(self.view, self.savvy_settings.get("graph_show_more_commit_info"))

    def build_git_command(self):
        args = self.savvy_settings.get("git_graph_args")
        follow = self.savvy_settings.get("log_follow_rename")
        if self.file_path and follow:
            args.insert(1, "--follow")

        if self.view.settings().get("git_savvy.log_graph_view.all_branches"):
            args.insert(1, "--all")

        author = self.view.settings().get("git_savvy.log_graph_view.filter_by_author")
        if author:
            args.insert(1, "--author={}".format(author))

        branch = self.view.settings().get("git_savvy.log_graph_view.filter_by_branch")
        if branch:
            args.append(branch)

        if self.file_path:
            file_path = self.get_rel_path(self.file_path)
            args = args + ["--", file_path]

        return args


class GsLogGraphCommand(GsLogCommand):
    """
    Defines the main menu if you invoke `git: graph` or `git: graph current file`.

    Accepts `current_file: bool` or `file_path: str` as (keyword) arguments, and
    ensures that each of the defined actions/commands in `default_actions` are finally
    called with `file_path` set.
    """
    default_actions = [
        ["gs_log_graph_current_branch", "For current branch"],
        ["gs_log_graph_all_branches", "For all branches"],
        ["gs_log_graph_by_author", "Filtered by author"],
        ["gs_log_graph_by_branch", "Filtered by branch"],
    ]


class GsLogGraphCurrentBranch(LogGraphMixin, WindowCommand, GitCommand):
    pass


class GsLogGraphAllBranches(LogGraphMixin, WindowCommand, GitCommand):

    def prepare_target_view(self, view):
        view.settings().set("git_savvy.log_graph_view.all_branches", True)


class GsLogGraphByAuthorCommand(LogGraphMixin, WindowCommand, GitCommand):

    """
    Open a quick panel containing all committers for the active
    repository, ordered by most commits, Git name, and email.
    Once selected, display a quick panel with all commits made
    by the specified author.
    """

    def run_async(self):
        email = self.git("config", "user.email").strip()
        self._entries = []

        commiter_str = self.git("shortlog", "-sne", "HEAD")
        for line in commiter_str.split('\n'):
            m = re.search(r'\s*(\d*)\s*(.*)\s<(.*)>', line)
            if m is None:
                continue
            commit_count, author_name, author_email = m.groups()
            author_text = "{} <{}>".format(author_name, author_email)
            self._entries.append((commit_count, author_name, author_email, author_text))

        self.window.show_quick_panel(
            [entry[3] for entry in self._entries],
            self.on_author_selection,
            flags=sublime.MONOSPACE_FONT,
            selected_index=(list(line[2] for line in self._entries)).index(email)
        )

    def on_author_selection(self, index):
        if index == -1:
            return
        self._selected_author = self._entries[index][3]
        super().run_async()

    def prepare_target_view(self, view):
        view.settings().set("git_savvy.log_graph_view.filter_by_author", self._selected_author)


class GsLogGraphByBranchCommand(LogGraphMixin, WindowCommand, GitCommand):
    _selected_branch = None

    def run_async(self):
        show_branch_panel(self.on_branch_selection, selected_branch=self._selected_branch)

    def on_branch_selection(self, branch):
        if branch:
            self._selected_branch = branch
            super().run_async()

    def prepare_target_view(self, view):
        view.settings().set("git_savvy.log_graph_view.filter_by_branch", self._selected_branch)


class GsLogGraphNavigateCommand(GsNavigate):

    """
    Travel between commits. It is also used by compare_commit_view.
    """
    offset = 0

    def get_available_regions(self):
        return self.view.find_by_selector("constant.numeric.graph.commit-hash.git-savvy")


class GsLogGraphCursorListener(EventListener, GitCommand):
    def is_applicable(self, view):
        # type: (sublime.View) -> bool
        settings = view.settings()
        return bool(
            settings.get("git_savvy.log_graph_view")
            or settings.get("git_savvy.compare_commit_view")
        )

    def on_activated(self, view):
        window = view.window()
        if not window:
            return

        if view not in window.views():
            return

        panel_view = window.find_output_panel('show_commit_info')
        if not panel_view:
            return

        # Do nothing, if the user focuses the panel
        if panel_view.id() == view.id():
            return

        # Auto-hide panel if the user switches to a different buffer
        if not self.is_applicable(view) and window.active_panel() == 'output.show_commit_info':
            window.run_command('hide_panel')
        # Auto-show panel if the user switches back
        elif (
            self.is_applicable(view)
            and window.active_panel() != 'output.show_commit_info'
            and self.savvy_settings.get("graph_show_more_commit_info")
        ):
            window.run_command("show_panel", {"panel": "output.show_commit_info"})

    # `on_selection_modified` triggers twice per mouse click
    # multiplied with the number of views into the same buffer,
    # hence it is *important* to throttle these events.
    # We do this seperately per side-effect. See the fn
    # implementations.
    def on_selection_modified_async(self, view):
        if not self.is_applicable(view):
            return

        draw_info_panel(view, self.savvy_settings.get("graph_show_more_commit_info"))
        # `colorize_dots` queries the view heavily. We want that to
        # happen on the main thread (t.i. blocking) bc it is way, way
        # faster.
        sublime.set_timeout(lambda: colorize_dots(view))

    def on_post_window_command(self, window, command_name, args):
        # type: (sublime.Window, str, dict) -> None
        view = window.active_view()
        if not view:
            return

        # If the user hides the panel via `<ESC>` or mouse click, remember the intent *if*
        # the `active_view` is a 'log_graph'
        if command_name == 'hide_panel' and self.is_applicable(view):
            self.savvy_settings.set("graph_show_more_commit_info", False)
            draw_info_panel(view, False)

        # If the user opens a different panel, don't fight with it.
        elif command_name == 'show_panel':
            # Note: 'show_panel' can also be used to actually *hide* a panel if you pass
            # the 'toggle' arg.
            show_panel = args.get('panel') == "output.show_commit_info"
            self.savvy_settings.set("graph_show_more_commit_info", show_panel)
            # Note: After 'show_panel' `on_selection_modified` runs *if* you used a
            # keyboard shortcut for it. If you open a panel via mouse it doesn't.
            # Since we cannot differentiate here, we do for now:
            if self.is_applicable(view):
                draw_info_panel(view, show_panel)


def colorize_dots(view):
    # type: (sublime.View) -> None
    dots = tuple(find_dots(view))
    _colorize_dots(view.id(), dots)


def find_dots(view):
    # type: (sublime.View) -> Set[colorizer.Char]
    return set(_find_dots(view))


def _find_dots(view):
    # type: (sublime.View) -> Iterator[colorizer.Char]
    for s in view.sel():
        line_region = view.line(s.begin())
        line_content = view.substr(line_region)
        idx = line_content.find(COMMIT_NODE_CHAR)
        if idx > -1:
            yield colorizer.Char(view, line_region.begin() + idx)


@lru_cache(maxsize=1)
# ^- throttle side-effects
def _colorize_dots(vid, dots):
    # type: (sublime.ViewId, Tuple[colorizer.Char]) -> None
    view = sublime.View(vid)
    view.add_regions('gs_log_graph_dot', [d.region() for d in dots], scope=DOT_SCOPE)
    paths = [c.region() for d in dots for c in colorizer.follow_path(d)]
    view.add_regions('gs_log_graph_follow_path', paths, scope=PATH_SCOPE)


def draw_info_panel(view, show_panel):
    """Extract line under the first cursor and draw info panel."""
    try:
        cursor = next(s.a for s in view.sel() if s.empty())
    except StopIteration:
        return

    line_span = view.line(cursor)
    line_text = view.substr(line_span)

    # Defer to a second fn to reduce side-effects
    draw_info_panel_for_line(view.window().id(), line_text, show_panel)


@lru_cache(maxsize=1)
# ^- used to throttle the side-effect!
# Read: distinct until      (wid, line_text, show_panel) changes
def draw_info_panel_for_line(wid, line_text, show_panel):
    window = sublime.Window(wid)

    if show_panel:
        commit_hash = extract_commit_hash(line_text)
        window.run_command("gs_show_commit_info", {"commit_hash": commit_hash})
    else:
        if window.active_panel() == "output.show_commit_info":
            window.run_command("hide_panel")


def extract_commit_hash(line):
    match = COMMIT_LINE.search(line)
    return match.groupdict()['commit_hash'] if match else ""


class GsLogGraphToggleMoreInfoCommand(TextCommand, WindowCommand, GitCommand):

    """
    Toggle global `graph_show_more_commit_info` setting. Also used by compare_commit_view.
    """

    def run(self, edit):
        show_panel = not self.savvy_settings.get("graph_show_more_commit_info")
        self.savvy_settings.set("graph_show_more_commit_info", show_panel)
        draw_info_panel(self.view, show_panel)


class GraphActionMixin(GsLogActionCommand):
    def run(self):
        view = self.window.active_view()

        self.selections = view.sel()
        if not len(self.selections) == 1:
            self.window.status_message("You can only do actions on one commit at a time.")
            return

        lines = util.view.get_lines_from_regions(view, self.selections)
        line = lines[0]

        commit_hash = extract_commit_hash(line)
        if not commit_hash:
            return

        self._commit_hash = commit_hash
        self._file_path = self.file_path

        super().run(commit_hash=self._commit_hash, file_path=self._file_path)


class GsCompareCommitActionCommand(GraphActionMixin):
    default_actions = [
        ["show_commit", "Show commit"],
        ["checkout_commit", "Checkout commit"],
        ["cherry_pick", "Cherry-pick commit"],
        ["compare_against", "Compare commit against ..."],
        ["copy_sha", "Copy the full SHA"]
    ]


class GsLogGraphActionCommand(GraphActionMixin):
    ...
