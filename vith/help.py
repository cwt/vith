HELP_TEXT = """\
Vith Help
==========

Vith is a Pure Python Vim Clone with Thai & Lao support (fork of PyVim).

Ex Commands (`:`)
-----------------

Files, Write & Quit:
    :e[!] [file]           Edit file (or reload current file)
    :enew[!]               Open a new untitled buffer in the current window
    :f / :file [name]      Show file info or rename buffer without writing
    :[addr]r [file|!cmd]   Read file or shell command output after [addr]
    :[range]w[!] [file]    Write buffer or range (copy if another file)
    :[range]w[!] >> file   Append buffer or range to file
    :[range]w !cmd         Pipe buffer or range to shell command stdin
    :sav[!] file           Write buffer and switch buffer name to file
    :up[!] [file]          Write only if buffer has unsaved changes
    :x[!] / :wq[!] [file]  Write (if modified / always) and quit
    :wa[!] / :xa[!] / :wqa Write all / write modified & quit all / write & quit all
    :q[!] / :qa[!] / :cq   Quit window/editor / quit all / quit with error code
    :so / :source [file]   Reload TOML or Python config file
    :pwd / :cd [dir]       Print or change working directory

Special Filenames (`%`, `#`):
    % / #                  Current / alternate buffer filename (in file & shell commands)
    :p :h :t :r :e         Chainable modifiers (abspath, dirname, basename, stem, ext)
    \\% / \\#               Literal % or #

Buffers, Windows & Tabs:
    :ls / :files / :buffers  List open buffers in a split
    :b[!] [name|N|#]         Switch buffer by name, 1-based index, or alternate (#)
    :bn / :bp / :bf / :bl    Next / previous / first / last buffer
    :bd[!] / :bw[!] [target] Delete / wipe current or targeted buffer
    :sb [target] / :badd f   Split buffer / add buffer without switching
    :sp / :vsp / :new / :vnew  Horizontal / vertical splits
    :clo[!] / :on[!] / :hide  Close window / keep only active window / hide
    :tabe / :tabc[!] / :tabo[!]  New tab / close tab / close other tabs
    :tabn / :tabp / :tabfir / :tabl  Navigate tabs
    :tabm [N|+N|-N|$] / :tabs        Move tab / list tabs in a split

Ranges & Line Editing:
    Addresses: N, ., $, %, '<, '>, +N, -N (e.g. :$, :+3, :'<,'>)
    :[range]s/pat/rep/[g][i][I][n][p]  Regex substitute (any delimiter)
    :[range]g[!]/pat/[cmd] / :v/...    Global / inverse global (d, y, p, s, m, t, norm)
    :[range]d [reg] / :y [reg]         Delete / yank lines to a register
    :[addr]pu[!] [reg]                 Put register/clipboard after (or before with !) [addr]
    :[range]m addr / :[range]t addr    Move / copy (:co) lines after addr (0 allowed)
    :[range]j[!] [count]               Join lines (! joins verbatim)
    :[range]sort[!] [i][u][r][n]       Sort lines (ignore-case, unique, reverse, numeric)
    :[range]retab[!] [N]               Re-expand tabs and spaces
    :[range]!cmd                       Filter range through shell command
    :[range]norm[!] keys               Replay Normal-mode keys per line
    :u / :undo, :red / :redo           Undo / redo last change
    :noh / :nohlsearch                 Temporarily clear search highlight

Options (`:set` / `:setlocal` / `:setl`):
    :set / :set all        Show changed options / all options in a split
    :set opt?              Query option value
    :set opt! / :set invopt  Toggle boolean option
    :set nu rnu ts=4 et    Multiple options in one command
    Toggles: number (nu), relativenumber (rnu), hlsearch (hls), incsearch (is),
             ignorecase (ic), smartcase (scs), expandtab (et), autoindent (ai),
             wrap, breakindent (bri), list, paste, ruler (ru), wildmenu (wmnu),
             cursorline (cul), cursorcolumn (cuc), mouse, jedi, tildeop (top),
             readonly (ro)
    Values:  tabstop (ts), shiftwidth (sw), scrolloff (so), colorcolumn (cc),
             filetype (ft), fileencoding (fenc), fileformat (ff)

Inspection & Misc:
    :reg / :registers      Show unnamed & named registers in a split
    :marks                 Show buffer marks (m{a-z}, '{a-z}, `{a-z}, '<, '>) in a split
    :mes / :messages       Show message history in a split
    :ver / :version        Show vith/Python/backend version info in a split
    :comp / :compression   Show cold-text compression statistics in a split
    :render[!]             Live async preview split (default: vertical; ! closes)
    :render-split-v[!]     Live async vertical preview split (editor left, preview right)
    :render-split-h[!]     Live async horizontal preview split (editor top, preview bottom)
    :colo[rscheme] name    Switch colorscheme
    :!cmd                  Run interactive shell command
    :h / :help             Show this help


Thanks to:
    - Jonathan Slenders: original author of PyVim and prompt_toolkit.
    - Pyflakes: the tool for checking Python source files for errors.
    - Jedi: the Python autocompletion library.
    - Pygments: Python syntax highlighter.
    - prompt_toolkit: the terminal UI toolkit."""
