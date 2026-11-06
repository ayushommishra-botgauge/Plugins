# botgauge user guide

botgauge is an add-on for Claude Code. Before your code goes to GitHub, it reads what you changed and lists the problems it finds, from serious bugs to small wording issues. Claude can then fix them for you. botgauge also stops a push to GitHub while a serious problem is still open, so broken code doesn't reach the rest of the team.

This guide covers installing botgauge, turning it on in a project, and using it every day. You don't need to know git well to follow it. If a step is unclear, ask a developer on your team.

## Contents

1. [Quick start](#quick-start)
2. [What botgauge checks](#what-botgauge-checks)
3. [Before you start](#before-you-start)
4. [Install botgauge on your computer](#install-botgauge-on-your-computer)
5. [Turn botgauge on in a project](#turn-botgauge-on-in-a-project)
6. [Everyday use](#everyday-use)
7. [When a finding isn't worth fixing](#when-a-finding-isnt-worth-fixing)
8. [What happens when you push](#what-happens-when-you-push)
9. [All commands](#all-commands)
10. [Teach botgauge your project's rules](#teach-botgauge-your-projects-rules)
11. [Cost](#cost)
12. [Updating and removing](#updating-and-removing)
13. [Common questions and problems](#common-questions-and-problems)

## Quick start

If you've done this before, these are the only steps:

1. In Terminal, run these two lines:
   ```
   claude plugin marketplace add ayushommishra-botgauge/Plugins
   claude plugin install botgauge@botgauge-plugins
   ```
2. In Claude Code, open your project and type `/botgauge:setup` (once per project).
3. Before each push, type `/botgauge:review`, then `/botgauge:fix-blockers` and `/botgauge:fix-high`.
4. Push as usual.

## What botgauge checks

Each problem botgauge finds is called a **finding**. Every finding gets a level and a short ID, such as **B1** or **H2**. The letter tells you the level:

| Level | IDs | What it means | Examples | Your push |
|---|---|---|---|---|
| 🔴 Merge blocker | B1, B2… | Must be fixed before the code goes to GitHub | Failing tests; a bug that breaks a feature; a security hole; a password left in the code | Stopped until it's fixed or dismissed |
| 🟠 High priority | H1, H2… | Should be fixed before you push | A crash in an unusual case; documentation that says the wrong thing; a bug fix with no test | Allowed, but fix these first |
| 🟡 Medium | M1, M2… | Fix it if it's quick | Missing documentation; repeated code; weak tests | Allowed |
| ⚪ Low | L1, L2… | Optional polish (at most 5 are shown) | Naming, wording | Allowed |
| 🟣 Pre-existing | P1, P2… | An old problem your change didn't cause | Anything above, in code you didn't touch | Never affects your push |

The areas botgauge looks at:

| Area | What it looks for |
|---|---|
| `checks` | The project's own tests and code-style checks |
| `bugs` | Mistakes that make the code do the wrong thing |
| `security` | Holes an attacker could use, and passwords or keys left in the code |
| `api` | Changes that would break other people's code that uses this project (only for projects published as a package) |
| `docs` | Documentation in the code that's missing, or that says something the code doesn't do |
| `tests` | Missing or weak tests |
| `quality` | Repeated or needlessly complicated code, and unclear names |
| `performance` | Slow server code, such as one database query for every item in a list |
| `frontend-api` | Wasteful requests from a web app, such as the same request sent twice or on every screen refresh |

Serious findings are double-checked by separate Claude reviewers before you see them, to cut down on false alarms.

## Before you start

You need these once.

- **A Mac.** botgauge has been tested on macOS. It should also work on Linux; Windows hasn't been tested.
- **Claude Code**, signed in with your work account. botgauge runs inside Claude Code: in the Claude Code extension for VS Code (or for Cursor or Windsurf), or in Claude Code in Terminal. VS Code without the Claude Code extension can't run it.
- **The `claude` command in Terminal.** To check, type `claude --version` in Terminal and press Enter. If you see a version number, you're set. If you see `command not found`, install it with this line, then close and reopen Terminal:
  ```
  curl -fsSL https://claude.ai/install.sh | bash
  ```
- **git and Python 3.** Type `git --version` and then `python3 --version` in Terminal. If your Mac offers to install the "command line developer tools", click **Install** and wait for it to finish.

To open Terminal: in VS Code, choose **Terminal → New Terminal** from the menu bar. Or press ⌘ Space, type "Terminal" and press Enter.

## Install botgauge on your computer

You do this once per computer. botgauge is published on GitHub at [ayushommishra-botgauge/Plugins](https://github.com/ayushommishra-botgauge/Plugins), and you don't need a GitHub account to install it.

1. **Install it.** Copy these two lines into Terminal, pressing Enter after each one:
   ```
   claude plugin marketplace add ayushommishra-botgauge/Plugins
   claude plugin install botgauge@botgauge-plugins
   ```
   The first line tells Claude Code where to find botgauge, and the second installs it. You should see:
   ```text
   ✔ Successfully installed plugin: botgauge@botgauge-plugins (scope: user)
   ```
2. **Restart Claude Code.** Start a new Claude Code chat, or quit and reopen VS Code.
3. **Check that it worked.** In the Claude Code chat box, type `/botgauge:`. A list of botgauge commands appears.

### If someone sent you a zip file instead

1. In Finder, choose **Go → Home** from the menu bar, and create a folder there named `claude-plugins`.
2. Move the zip file into `claude-plugins` and double-click it. A folder appears, usually named `skills`.

   > Keep this folder where it is. Claude Code uses botgauge straight from it, so moving, renaming or deleting the folder breaks botgauge.

3. In Terminal, run this line. If the folder has a different name, use that name instead of `skills`:
   ```
   claude plugin install botgauge --marketplace ~/claude-plugins/skills
   ```
4. Restart Claude Code and check that it worked, as in steps 2 and 3 above.

## Turn botgauge on in a project

A **project** here means a folder of code tracked with git, such as your team's backend or web app. You do this once for each project on your computer.

Open the project in VS Code and look at the list of files on the left.

### If you see a `.botgauge` folder and a `REVIEW.md` file

A teammate has already set up this project. You only need to switch on the push check for yourself:

1. In Claude Code, type `/botgauge:setup` and press Enter.
2. Claude confirms that the push check is on. That's it.

### If you don't see them

You're the first person to use botgauge in this project.

1. Ask Claude to make a new branch for the setup, for example: *"Create a new branch called botgauge-setup."*
2. Type `/botgauge:setup` and press Enter.
3. Claude studies the project for a minute, then shows a short summary: the commands the project uses for tests and code style, the folders that need extra care (such as login, payments or personal data), and the documentation style.
4. Claude asks two questions:
   - **Write this setup?** Choose **Write it**, or **Change something** if a detail looks wrong.
   - **Pre-push check:** choose **Block** (recommended). It stops a push while a merge blocker is open. **Warn** only shows the problems and lets the push through. **Off** does nothing when you push.
5. Claude creates the files. Claude Code may ask you to approve each one.
6. Claude offers to run the project's checks once, to make sure they work. Say yes.
7. Share the setup with your team. Ask Claude: *"Commit the botgauge setup files and open a pull request."* Or ask a developer to do it. Once the pull request is merged, teammates only need the two steps in the section above.

The setup creates or changes these files:

| File | What it's for |
|---|---|
| `.botgauge/config.json` | The project's botgauge settings |
| `.botgauge/pre-push` | The push check |
| `.botgauge/.gitignore` | Keeps your personal findings out of git |
| `REVIEW.md` | The project's review rules, in plain English ([more below](#teach-botgauge-your-projects-rules)) |
| `CLAUDE.md` | A short list of the project's checks, for Claude |

## Everyday use

Before every push:

1. **Make your changes** as usual, with Claude or by hand.
2. **Review them.** Type `/botgauge:review`. A small change usually takes under a minute; bigger ones take a few minutes.
3. **Fix what matters.** Type `/botgauge:fix-blockers` if there are any 🔴 findings, then `/botgauge:fix-high` for 🟠 ones. Use `/botgauge:fix-medium` and `/botgauge:fix-low` if you have time.
4. **Commit and push** as usual.

botgauge only checks what changed since your last review. Running it often is quick, and you never see the same finding twice.

You can also ask in plain words, such as *"review my changes"* or *"can I push?"*. Fixing and dismissing always need the command.

### Reading a report

Here's an example report for a change that added discount codes to an online shop:

```text
botgauge review: 6 to fix — 1 merge blocker, 1 high, 3 medium, 1 low.
Checked 1 file (+13 −2) on discount-codes · security, bugs, performance, tests, docs, quality · quick depth
Push: blocked until B1 is fixed or dismissed

🔴 Merge blockers
  B1   src/orders.py:6  · bugs
      Order totals ignore item quantity
      Problem: The total adds up prices only, so any item with a quantity above 1 is undercharged.
      When: An order of 2 items at 500 cents each totals 500 instead of 1000.
      Fix: Multiply each price by its quantity again, as before.

🟠 High priority
  H1   src/orders.py:8  · bugs
      An unknown discount code crashes checkout
      Problem: DISCOUNTS[discount_code] raises KeyError for a code that isn't in the table.
      When: A customer types a mistyped code such as WELCOM10.
      Fix: Reject unknown codes with a clear error message.

🟡 Medium
  M1   src/orders.py:7  · tests
      No test covers the discount path
      Problem: Nothing checks that a valid code lowers the total.
      Fix: Add a test for WELCOME10 and for an unknown code.
  M2   src/orders.py:5  · docs
      order_total's docstring doesn't mention discount_code
      Problem: The new argument and the discounted result aren't described.
      Fix: Add an Args entry for discount_code.
  M3   src/orders.py:12  · docs
      New public function apply_coupon has no docstring
      Problem: Callers can't tell what it changes or returns.
      Fix: Add a short Google-style docstring.

⚪ Low
  L1   src/orders.py:12  · quality
      code and discount_code name the same thing
      Problem: Two names for one idea make the code harder to follow.
      Fix: Call it discount_code in both places.

Next: /botgauge:fix-blockers · /botgauge:fix-high · /botgauge:fix-medium · /botgauge:fix-low · not a real problem? /botgauge:dismiss <ID> wrong|intentional|not-worth-it "why"

Cost: $0.14 across 4 Claude calls (at API prices; on a Claude plan it counts toward usage).
```

How to read it:

- **The first line** counts the findings at each level.
- **Push** says whether `git push` would go through right now.
- **Each finding** starts with its ID, the file and line (`src/orders.py:6` means line 6 of that file), the area, and a title. Then:
  - **Problem** says what's wrong.
  - **When** gives a real situation where it goes wrong.
  - **Fix** says what to change.
- **Next** lists the commands to run next.
- **Cost** is explained in [Cost](#cost).

### Fixing findings

- `/botgauge:fix-blockers` fixes every open merge blocker.
- `/botgauge:fix-high`, `/botgauge:fix-medium` and `/botgauge:fix-low` do the same for their level.
- `/botgauge:fix B1 H2` fixes only the findings you name.

Claude makes the smallest change that solves each problem. Claude Code may ask you to approve each edit. Then botgauge re-checks just those findings and shows the result:

```text
botgauge fix: 1 fixed.
Fixed: B1
Push: allowed
```

In three cases, Claude does more than make the edit:

- **The fix changes how other people's code uses this project.** For example, it renames something they depend on. Claude asks you first.
- **A password or key was found in the code.** Claude removes it, but you also need to replace that password or key (ask whoever manages it), because the old one is still in the project's history.
- **New serious problems keep appearing in the same area after every round of fixes.** Claude asks whether to keep patching or to step back and rethink the approach.

## When a finding isn't worth fixing

Close it with a reason instead:

```
/botgauge:dismiss L1 not-worth-it "we rename these in the next cleanup"
```

botgauge replies:

```text
L1 dismissed (not-worth-it): we rename these in the next cleanup
```

Choose one of three reasons:

- `wrong`: it isn't actually a problem (a false alarm).
- `intentional`: the code does this on purpose.
- `not-worth-it`: it's real, but not worth fixing now.

A dismissed finding doesn't come back. A dismissed merge blocker no longer stops your push, so only dismiss one when you're sure.

## What happens when you push

botgauge's push check runs first, whether you type `git push` or use VS Code's **Sync** or **Push** button:

- **No merge blockers are open, and your changes have been checked.** The push goes straight through.
- **A 🔴 merge blocker is open.** The push stops, and you see:
  ```text
  botgauge: push stopped — 1 merge blocker open on discount-codes:
    B1   src/orders.py:6  Order totals ignore item quantity
  Fix them with /botgauge:fix-blockers in Claude Code, or if one isn't a real problem:
    /botgauge:dismiss <ID> wrong|intentional|not-worth-it "why"
  Skip this check once: git push --no-verify
  ```
  Run `/botgauge:fix-blockers`, or dismiss the finding, then push again.
- **Your latest changes haven't been checked yet.** botgauge checks them for merge blockers first, which usually takes a few minutes. Then the push goes through, or stops as above.

In an emergency, `git push --no-verify` skips the check once. Use it rarely, because the problems are still there.

## All commands

Type these in the Claude Code chat box. `<ID>` means a finding's ID, such as `B1` or `H2`.

| Command | Example | What it does |
|---|---|---|
| `/botgauge:review` | `/botgauge:review` | Checks your changes for problems at every level and lists them as merge blockers, high, medium and low. You can also just ask Claude to "review my changes". |
| `/botgauge:review-blockers` | `/botgauge:review-blockers --quick` (one fast pass) | Checks only for merge blockers: failing tests or checks, bugs, security problems, breaking API changes and runaway web requests. Faster and cheaper than a full review. It's also what the push check runs. |
| `/botgauge:review-high` | `/botgauge:review-high security` (security only) | Checks only for high-priority problems. |
| `/botgauge:review-medium` | `/botgauge:review-medium docs tests` (documentation and tests only) | Checks only for medium problems. |
| `/botgauge:review-low` | `/botgauge:review-low src/` (the `src` folder only) | Checks only for low problems, such as naming and wording. |
| `/botgauge:status` | `/botgauge:status` | Lists every open finding, and says whether `git push` would go through right now. You can also ask Claude "can I push?". |
| `/botgauge:status <level>` | `/botgauge:status blockers` | Lists one level only: `blockers`, `high`, `medium`, `low` or `pre-existing`. |
| `/botgauge:status --detail` | `/botgauge:status high --detail` (high findings, in full) | Lists the open findings with all their details: the problem, an example of when it goes wrong, and the suggested fix. |
| `/botgauge:fix-blockers` | `/botgauge:fix-blockers` | Fixes every open merge blocker, then re-checks them. |
| `/botgauge:fix-high` | `/botgauge:fix-high` | Fixes every open high-priority finding, then re-checks them. |
| `/botgauge:fix-medium` | `/botgauge:fix-medium` | Fixes every open medium finding, then re-checks them. |
| `/botgauge:fix-low` | `/botgauge:fix-low` | Fixes every open low finding, then re-checks them. |
| `/botgauge:fix <ID> <ID>…` | `/botgauge:fix B1 H2` | Fixes only the findings you name. This is the only way to fix pre-existing findings (P IDs). With no IDs, it lists what's open and asks which to fix. |
| `/botgauge:dismiss <ID> <reason> "why"` | `/botgauge:dismiss H2 intentional "retries are safe here"` | Closes a finding without fixing it. The reason is `wrong` (not a real problem), `intentional` (the code does this on purpose) or `not-worth-it` (real, but not worth fixing now). A dismissed finding doesn't come back. |
| `/botgauge:setup` | `/botgauge:setup` | In a new project: studies it, writes botgauge's settings and review rules, and switches on your push check. In a project that's already set up: just switches on your push check. |
| `/botgauge:setup --update` | `/botgauge:setup --update` | Refreshes the project's setup after botgauge is updated. It shows what would change before writing anything. |
| `/botgauge:setup --remove` | `/botgauge:setup --remove` | Switches off your push check in this project. Do this before uninstalling botgauge. |

Every review command (`/botgauge:review` and the four `review-` commands) also accepts these options:

| Option | What it does | Example |
|---|---|---|
| `--quick` | One faster, cheaper pass. Good for tiny changes. | `/botgauge:review --quick` |
| `--deep` | The most careful, and most expensive, review. Use it for risky changes. | `/botgauge:review --deep` |
| `--full` | Checks everything on your branch again, not just what changed since the last review. | `/botgauge:review --full` |
| An area name | Checks only that area: `checks`, `bugs`, `security`, `api`, `docs`, `tests`, `quality`, `performance` or `frontend-api`. | `/botgauge:review security` |
| A folder | Checks only that folder. | `/botgauge:review src/payments` |

You can combine them, for example `/botgauge:review security --deep src/payments`.

Commands you type in Terminal:

| Command | What it does |
|---|---|
| `claude plugin marketplace add ayushommishra-botgauge/Plugins` | Tells Claude Code where to find botgauge on GitHub. Needed once, before installing. |
| `claude plugin install botgauge@botgauge-plugins` | Installs botgauge on your computer. |
| `claude plugin marketplace update botgauge-plugins` | Fetches the latest version list from GitHub. Run it before updating. |
| `claude plugin update botgauge@botgauge-plugins` | Updates botgauge to the latest version. |
| `claude plugin list` | Lists the installed plugins, so you can check that botgauge is there and enabled. |
| `claude plugin uninstall botgauge@botgauge-plugins` | Removes botgauge from your computer. Run `/botgauge:setup --remove` in each project first. |
| `claude plugin marketplace remove botgauge-plugins` | Makes Claude Code forget where botgauge lives. Run it after uninstalling. |
| `git push --no-verify` | Pushes without the push check, just this once. For emergencies only. |

## Teach botgauge your project's rules

Each project has a `REVIEW.md` file with its review rules, written in plain English. botgauge reads it before every review. Edit it when:

- **botgauge keeps flagging something that's fine in this project.** Add a line under **Don't report**.
- **botgauge missed something that matters.** Add a line under **Always check**.
- **Something is more or less serious here than usual.** Add a line under **Severity overrides**, for example: *"Missing pagination on list endpoints is high here, not medium."*

Commit your changes to `REVIEW.md` so the whole team gets them.

## Cost

- botgauge uses your Claude plan, like anything else you do in Claude Code.
- Each report ends with a **Cost** line: what that review would have cost at Claude's API prices. It's there so you can compare reviews; it isn't a bill.
- For a small change, a review typically costs $0.10–$0.75 at those prices. Bigger changes cost more.
- botgauge reviews code that handles logins, payments, passwords or personal data more carefully, which costs more.
- To keep usage low:
  - Review as you go, since only new changes are checked.
  - Use `--quick` for tiny changes.
  - Save `--deep` for risky ones.

## Updating and removing

**Getting an update.**

1. In Terminal, run these two lines:
   ```
   claude plugin marketplace update botgauge-plugins
   claude plugin update botgauge@botgauge-plugins
   ```
   If you installed from a zip file, do this instead: delete the old folder inside `claude-plugins`, then unzip the new zip file in the same place, under the same name.
2. Start a new Claude Code chat.
3. If the update notes say the push check changed, one person runs `/botgauge:setup --update` in each project and commits the result.

**Removing botgauge.**

1. In each project where you used it, type `/botgauge:setup --remove`. Do this first. Otherwise the push check stays in the project and keeps stopping pushes it can no longer check.
2. In Terminal, run these two lines:
   ```
   claude plugin uninstall botgauge@botgauge-plugins
   claude plugin marketplace remove botgauge-plugins
   ```
3. If you installed from a zip file, also delete its folder inside `claude-plugins`.

## Common questions and problems

**Does botgauge work in VS Code?**
Yes, through the Claude Code extension: type the `/botgauge:` commands in the Claude Code chat panel. It also works in Cursor and Windsurf, which use the same extension, and in Claude Code in Terminal. VS Code on its own, for example with only GitHub Copilot, can't run botgauge, because Claude does the reviewing. The only step that needs Terminal is installing botgauge, and VS Code has a terminal built in (**Terminal → New Terminal**).

**Do teammates who don't use Claude Code need to do anything?**
No. The push check only switches on for people who run `/botgauge:setup`, so everyone else pushes as before. There's one exception. Some projects, mostly JavaScript ones, use a tool called Husky to share git hooks with everyone; they have a `.husky` folder. In those projects, setting up botgauge switches the push check on for the whole team, and teammates without Claude Code will have their pushes stopped. Check with your team before you set up botgauge in one of them.

**The `/botgauge:` commands don't appear.**
Start a new Claude Code chat. If they're still missing, run `claude plugin list` in Terminal; botgauge should be listed as enabled. If it isn't, repeat [the install steps](#install-botgauge-on-your-computer).

**Terminal says `claude: command not found`.**
Install the `claude` command (see [Before you start](#before-you-start)), then close and reopen Terminal.

**My push was stopped.**
Type `/botgauge:status blockers` to see why. Then run `/botgauge:fix-blockers`, or dismiss a finding that isn't real.

**My push is taking a long time.**
botgauge is checking changes it hasn't seen yet. That usually takes a few minutes and never more than 15. In Terminal, Ctrl+C cancels the push.

**The review says "Nothing new to review".**
Everything was already checked in your last review. To check everything again, type `/botgauge:review --full`.

**Claude reminds me to run `/botgauge:setup`.**
The project uses botgauge, but the push check isn't on for you yet. Run `/botgauge:setup` once.

**A finding is wrong.**
Dismiss it with the `wrong` reason and say why. If the same kind of false alarm keeps coming up, add a line to `REVIEW.md` under **Don't report**.

**I installed from a zip file, then moved or deleted its folder.**
Put the folder back in `claude-plugins` in your home folder, or unzip the zip file there again, then start a new chat.

**Where are my findings saved?**
On your computer only, in the project's `.botgauge/local` folder. They're never committed or shared.

**Does botgauge send my code anywhere?**
Only to Claude, the same way Claude Code does whenever you ask it something. Nothing goes to GitHub until you push.

**Does this replace the review on pull requests?**
No. botgauge checks your work before you push. Whatever reviews your team runs on pull requests still happen on GitHub.