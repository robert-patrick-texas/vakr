# Screen-reader review of the web UI

This is the manual part of WEB-T08 and WEB-T09: a person with a screen reader works through every core workflow and records what is announced. The automated part (axe, the keyboard path, non-colour state) runs on every change in `web/e2e/a11y.spec.ts`, `kiosk-a11y.spec.ts`, and `dashboards-a11y.spec.ts`. It catches markup errors but cannot tell whether a page makes sense when heard.

> **Status: not yet run. Follow-up action.** No screen reader was available on 2026-10-21. Run this review before MVP acceptance (blueprint section 26, criterion 8: "Core web and public workflows meet the accessibility acceptance criteria"). Appendix E.31 decision 18 and [the traceability table](../traceability.md) (UI-011, WEB-T08, WEB-T09) track it. When it has been run, record the results below and update both.

Contents:

- [What you need](#what-you-need)
- [Setting up a test instance](#setting-up-a-test-instance)
- [How to work through the script](#how-to-work-through-the-script)
- [The script](#the-script)
- [Recording the results](#recording-the-results)
- [After the review](#after-the-review)

## What you need

Use at least two of these combinations, one on Windows or Linux and VoiceOver on macOS. They cover the main browser accessibility engines.

| Screen reader | Browser | Platform | Cost |
|---|---|---|---|
| NVDA (current release) | Firefox, then Chrome | Windows | Free |
| VoiceOver | Safari | macOS | Built in |
| Orca | Firefox | Ubuntu desktop (Super+Alt+S starts it) | Built in |
| JAWS | Chrome | Windows | Licensed; optional |

You also need:

- An admin and an operator account on the test instance (below).
- About three hours for one combination, or two for each further one: the first pass takes longest.
- A way to note exactly what was said. NVDA's speech viewer (NVDA menu → Tools → Speech viewer) and VoiceOver's caption panel let you copy the spoken text.

Turn off any browser extensions that change pages. Use the browser's default zoom unless a step says otherwise.

## Setting up a test instance

Use the seeded end-to-end fleet. It has devices in every state, interfaces with gaps and markers, an identity review, a WAN circuit, and an admin and an operator account. Do not use a production node: the review creates and deletes dashboards and kiosk tokens.

```console
$ cd ~/code/vakr && make web-build
$ export VAKR_E2E_ADMIN_PASSWORD=$(openssl rand -base64 18)      # never commit or print it
$ export VAKR_E2E_OPERATOR_PASSWORD=$(openssl rand -base64 18)
$ uv run python -m tests.e2e.serve --port 8080 --web-dir web/build --fixtures-out /tmp/fx.json
```

Open `http://127.0.0.1:8080/` and sign in as `e2e-admin` or `e2e-operator`. Plain HTTP from loopback is allowed for testing (E.30 decision 4). To test from another machine, for example a Mac running VoiceOver, put the instance behind the reverse proxy of [web access](../operations/web-access.md) and use its HTTPS address. Sign-in over plain HTTP from another machine is refused.

This serves the production build with the production Content-Security-Policy, so the review sees exactly what operators see. The Vite dev server (`npm run dev`) does not apply the CSP. Do not review against it.

## How to work through the script

- Work through the sections in order. Some steps depend on earlier ones: for example, section 9 uses the dashboard created in section 8.
- Navigate the way a screen-reader user would: by headings (H), landmarks (D in NVDA, the rotor in VoiceOver), form fields (F), tables (T), and Tab. Do not use the mouse.
- **Expected** says what must be announced or reachable. The exact wording can differ between screen readers. What matters is that the meaning is complete and unambiguous, and that nothing important is missing or announced twice.
- Mark each step **Pass**, **Fail**, or **Note**. Use Note for something that works but is confusing. For every Fail and Note, write down what was said and what you expected.
- Several pages refresh every 30 or 60 seconds. Stay on each of them for at least one minute, and confirm that a refresh neither moves focus nor repeats content (12.8: "Auto-refresh preserves keyboard focus and does not repeatedly announce unchanged content").

## The script

### 1. Every page (check on the first three pages you visit)

| # | Do | Expected |
|---|---|---|
| 1.1 | Load any page. | The page title is announced: "Overview · vakr", "Devices · vakr", and so on. Each page has its own title. |
| 1.2 | Press Tab once after the page loads. | "Skip to content, link". Enter moves focus to the main content, past the navigation. |
| 1.3 | List the landmarks. | There is a banner, navigation named "Main", a search landmark, and the main region. There are no unnamed duplicate landmarks. |
| 1.4 | List the headings. | There is one level-1 heading, naming the page. Panels and sections are level 2 or deeper, with no skipped levels that hide structure. |
| 1.5 | Tab through the main navigation. | Every item is read as a link with its name. The current section is announced as "current page". Admin appears only for an admin. |
| 1.6 | Find the search box. | It is read as an edit field with a label. Submitting it goes to the search results. |
| 1.7 | Find your name, role, and release in the bar. | They are read as text, and **Sign out** is read as a button or link. |
| 1.8 | Set "reduce motion" in the operating system and reload. | Nothing animates: no sliding, fading, or moving progress indicators. |

### 2. Signing in and out

| # | Do | Expected |
|---|---|---|
| 2.1 | Open `/login`. | The username and password fields are announced with their labels. The password field is announced as protected or secure. |
| 2.2 | Sign in with a wrong password. | "Sign-in failed: check the username and password." is announced at once, as an alert, without moving focus. It does not say whether the username exists. |
| 2.3 | Sign in correctly from a deep link, for example `/devices`, opened while signed out. | You arrive at the page you asked for, and its title is announced. |
| 2.4 | Sign out. | You are told you are signed out, or you land on the sign-in page with its title announced. |

### 3. Overview (`/`)

| # | Do | Expected |
|---|---|---|
| 3.1 | Read the page by headings. | Each panel has a heading: device counts, health, high priority, WAN circuits, top utilization, top errors, events. |
| 3.2 | Read the device counts. | Each count is read with its bucket name ("Attention", "Active", "Offline"). A count is never read without its meaning. |
| 3.3 | Read a status anywhere on the page. | It is read as words ("Down", "Unknown — collector unavailable", "Up"). The icon is not read separately, and colour is never the only cue (WEB-T09). |
| 3.4 | Open the health table ("Health of vakr itself"). | It is announced as a table with column headers: Component, State, Report age, Spool use. Each row header names the component. A missing age is read as "—" or "no report", never as 0. |
| 3.5 | Stay on the page for one minute. | The 30 s refresh does not move focus, does not re-read the page, and does not announce unchanged values. |

### 4. Device list (`/devices`)

| # | Do | Expected |
|---|---|---|
| 4.1 | Find the filters. | They are a group named "Device filters". Every control has a label, and each choice is announced with its state (checked or selected). |
| 4.2 | Change a filter. | The list updates. The address changes, so the view can be bookmarked. Focus stays on the control you changed. |
| 4.3 | Move into the table with the table navigation keys. | It is announced as a table with a caption, its row and column counts, and column headers. Moving across a row reads each header with its cell. |
| 4.4 | Tab to a sortable column header, for example Name, and press Enter. | It is read as a button that states the current order. After Enter, "Sorted by Name, A to Z" is announced once. The header's new state is read when you return to it. |
| 4.5 | Read a row in Attention. | The state is read as words first. Device name, address, site, ICMP and loss, SNMP, uptime, and age follow, each with its header. "—" or "no data" is read for missing values, never "0". |
| 4.6 | Go to the next page with the pager. | "Page 2 of devices" (or the actual page) is announced once, and focus stays on the pager. |
| 4.7 | Open a device from the list. | The link text names the device. The device page title is announced. |

### 5. Device page (`/devices/<uuid>`)

| # | Do | Expected |
|---|---|---|
| 5.1 | Read the header. | State in words, name, address, site, tags, admin state, collector and last poll. Each position's own verdict is read separately and named. |
| 5.2 | Read the facts. | ICMP, latency, jitter, SNMP, and `sysUpTime` are read with their units. The `sysUpTime` disclaimer is reachable. |
| 5.3 | Find the reachability graph. | The graph is announced as an image with a name that says what it shows. Directly after it there is a text description: resolution, display zone, and markers explained in words. A summary table gives exact values. |
| 5.4 | Use the graph's point slider. | It is read as a slider. Each step announces a time and a value in words ("12:05, 3.2 ms" or similar), not a bare number. A gap is announced as missing, never as 0. |
| 5.5 | Find the range buttons. | They are a group named "Graph range". The chosen range is announced as pressed. Choosing another announces "Range 24h" (or the range chosen) once. |
| 5.6 | Open the custom range. | The button reports expanded and collapsed. The from and to fields have labels. An invalid range is announced as an alert, and focus stays in the form. Applying it announces "Custom range applied". |
| 5.7 | Switch the interfaces between **Table** and **Graphs**. | The current view is announced. In Graphs, each card has a heading naming the interface. At most 12 cards are shown, and the page control is announced. |
| 5.8 | As an admin, press **Rediscover now**. | "Rediscovery requested" is announced. Progress (queued, running) is reachable as text without being announced every 2 s. When it ends, the outcome is announced once ("Succeeded: 24 interfaces, 1 new, …", or "Failed: " and the reason). |
| 5.9 | As an operator, look for **Rediscover now**. | It is not there. |
| 5.10 | Ask for a rediscovery the device refuses (as an admin, on a disabled device if the fleet has one). | The reason is announced as an alert. |

### 6. Interface page (`/interfaces/<uuid>`)

| # | Do | Expected |
|---|---|---|
| 6.1 | Read the identity and circuit metadata. | Name, alias, description, device, WAN circuit, provider, circuit ID, and contracted rate are each read with a label. |
| 6.2 | Read the traffic and error graphs. | Steps 5.3 to 5.6 hold for each graph. Interface gaps and invalid-baseline markers are described in words. |
| 6.3 | Read the state timeline. | The segment table is always present: start, end, admin state, oper state, and samples. "No data" is read for a stretch without samples, never "up". The ECharts picture beside it is decorative, or is described by the table. |
| 6.4 | Read the identity history. | It is a table with headers. Earlier `ifIndex` values are read with their dates. |
| 6.5 | As an admin, use **Pin to dashboard**. | The dashboard choice has a label. The result ("Pinned to <dashboard>.") is announced once. A conflict says to pin again. |

### 7. Identity reviews (`/identity-reviews`)

| # | Do | Expected |
|---|---|---|
| 7.1 | Read a review as an operator. | Candidates and their evidence are readable. There are no decision controls. |
| 7.2 | As an admin, find the decision controls. | They are a group named "Decision for ifIndex N". Each choice is a button that says what it does. |
| 7.3 | Make a decision. | The outcome is announced once. A failure is announced as an alert. |

### 8. Dashboards (`/dashboards`) as an admin

| # | Do | Expected |
|---|---|---|
| 8.1 | Read the dashboards list. | The built-in and custom dashboards are links with names and descriptions. |
| 8.2 | Create a dashboard (`/dashboards/new`). | Name, description, and refresh fields have labels. Each limit, such as 15 s to 1 h, is reachable as text, not only as a placeholder. |
| 8.3 | Add a pinned-interfaces widget and search for interfaces. | The results are a list named "Interfaces found". Choosing one announces it as pinned. Each pinned interface has buttons "Move <interface> up", "Move <interface> down", and "Unpin <interface>". |
| 8.4 | Add an interface-selection widget. | The site, tag, group, provider, WAN, and interest fields all have labels. "Rows shown" has a label and its limit. |
| 8.5 | Reorder the widgets. | The buttons are "Move widget 2 up" and similar. After a move, focus stays on the moved widget's control, or on the same button, and the new position can be heard. |
| 8.6 | Save. | "Saved <name>." is announced once. Invalid input is announced with the field named. |
| 8.7 | Open the same dashboard in two tabs, save in one, then save in the other. | The conflict is announced, and you are offered a reload. Nothing is silently overwritten. |
| 8.8 | View the dashboard (`/dashboards/<uuid>`). | Every widget has a heading. Tables follow 4.3 and 4.5. A pinned interface that no longer exists is read as "no longer exists or retired", in its place. |
| 8.9 | As an operator, view the same dashboard. | There are no edit, delete, or pin controls. |
| 8.10 | Delete the dashboard you made. | **Delete dashboard** is replaced by "Yes, delete <name>" and "Keep it", and focus does not get lost when they appear. "Deleted <name>." is announced. |

### 9. Admin (`/admin`)

| # | Do | Expected |
|---|---|---|
| 9.1 | Open `/admin` as an operator. | A permission-denied notice is announced as an alert. There are no admin controls. |
| 9.2 | As an admin, create a kiosk token named "review". | The label field has a label. The new token appears in a region named "New kiosk token" with the setup steps. The copy button says what it copies. It is clear the token is shown only once. |
| 9.3 | Read the kiosk token list. | It is a table: label, created by, created, expires, last used. No token value appears. |
| 9.4 | Revoke the "review" token. | **Revoke** asks for confirmation (with **Cancel**) before it acts, and focus does not get lost. The result is announced. |
| 9.5 | Read the recent operations table. | It is a table with headers. Each operation's state is read as words. |

### 10. Kiosk display (`/kiosk/...`)

Create a fresh kiosk token for this section, and revoke it at the end.

| # | Do | Expected |
|---|---|---|
| 10.1 | Open `/kiosk/setup` in a private window. | The token field is labelled "Kiosk token" and announced as protected. **Set up** is a button. |
| 10.2 | Paste a wrong token and submit. | The refusal is announced as an alert. |
| 10.3 | Paste the right token and submit. | "This display is set up." is announced. You reach the chooser, and its title is announced. |
| 10.4 | Open `/kiosk/overview`. | There is no navigation, sign-out, or links. The freshness line ("Data as of HH:MM:SS · refreshes every N s") is readable. Tables follow 4.3, and a table wider than the window is a named region you can scroll from the keyboard. |
| 10.5 | Stop the test instance for one minute, then start it again. | A stale notice is announced once when a refresh fails. The rows stay readable. The notice goes away after the next successful refresh, without moving focus. |
| 10.6 | Open `/kiosk/rotate?pages=overview,wan&every=20`. | The header says which dashboard this is and what comes next ("1 of 2 · next: WAN circuits in 15 s"). At each switch "Now showing WAN circuits" is announced once. It is not announced every second. |
| 10.7 | Use the rotation's **Pause** button. | It is a button that announces its state. "Rotation paused" and "Rotation resumed" are announced. |
| 10.8 | Revoke the token in another window, then wait for a refresh. | A full-screen notice says the display is signed out and what to do. It is announced once. |

### 11. Page states (UI-014)

| # | Do | Expected |
|---|---|---|
| 11.1 | Load a page on a slow connection (browser devtools → network throttling, "Slow 3G"). | "Loading" is announced, or the region reports busy. Content is not read twice when it arrives. |
| 11.2 | Filter the device list to something with no match. | An empty-state message is read instead of an empty table. |
| 11.3 | Open an interface whose telemetry is unavailable or partial (the fleet's partial device). | The partial or unavailable note is announced, and says what is missing. |
| 11.4 | Stop the test instance and navigate. | "Backend unavailable." is announced as an alert. The page does not present old data as current. |
| 11.5 | Open an address that does not exist, for example `/devices/not-a-uuid`. | A not-found notice is announced, and the page title says so. |

### 12. Non-colour state and zoom (WEB-T09, WCAG 1.4.1, 1.4.10)

These checks need sight but no screen reader. Do them in the same session.

| # | Do | Expected |
|---|---|---|
| 12.1 | Turn on greyscale (Windows: Settings → Accessibility → Colour filters; macOS: Accessibility → Display → Colour filters). Read the device list and the overview. | Every state can be told apart by its icon and words alone. |
| 12.2 | Zoom the browser to 200 %, then 400 %. | Nothing is cut off. Wide tables scroll inside their own region, and the page itself does not scroll sideways. |
| 12.3 | Use the Windows high-contrast or contrast theme. | Focus outlines, status icons, and button edges stay visible. |

### 13. Manual inventory entry as an admin (27.4, META-008, META-013)

| # | Do | Expected |
|---|---|---|
| 13.1 | Open **Devices** → **Add a device**. | Every field has a label and a note that says what it takes, read after the label. The admin state, reachability, site, and address family are selects with their choices read. |
| 13.2 | Submit with the name `core1` and the address `169.254.169.254`. | Focus moves to the "Not saved" summary, which is read as an alert. Each refused field is read as invalid, and its note starts with "Problem:". |
| 13.3 | Add a device with a new name and address and no credential. | "Added <name>" is announced, with the warning about the missing credential in words. "Open the device page" is a link. |
| 13.4 | On that device's page choose **Edit device**, change the comment, and save. | "Saved <name>" is announced; the page says what changed. |
| 13.5 | Open the edit page of a device `inventory.toml` manages. | The notice names the file and `vakr inventory apply`. Every field is read as dimmed or unavailable, and its note says "Managed by inventory.toml". There is no Save button. |
| 13.6 | Retire the device you added. | **Retire device** is replaced by "Yes, retire <name>" and "Keep it", and focus does not get lost. "Retired <name>" is announced. |
| 13.7 | On an interface page, read **Classification**, then change the annotation and save. | The interest, flags, vakr tags, description tags, and "Not yet classified" are read as words. The checkboxes "Sticky" and "WAN circuit" have labels. A rate such as `10 Mb` is refused beside its field before anything is sent. |
| 13.8 | On **Interfaces**, tick **Only not yet classified**, select two rows, add a tag, and apply. | Each row's checkbox is read as "Select <device> <interface>". The count of selected rows is read as it changes. "Tagged 2 interface(s)" is announced. |

## Recording the results

Copy this table once for each screen-reader and browser combination, into a new file `docs/development/screen-reader-results-YYYY-MM-DD.md`, and link it from here.

| Field | Value |
|---|---|
| Date | |
| Tester | |
| Screen reader and version | |
| Browser and version | |
| Operating system | |
| vakr commit (`git rev-parse --short HEAD`) | |

| Step | Result (Pass / Fail / Note) | What was announced, and what was expected instead |
|---|---|---|
| 1.1 | | |
| … | | |

Results so far: none (see the status note at the top).

## After the review

1. Open an issue for each Fail, quoting the step number and what was announced. Fix the issue, and add an automated test where the failure can be caught by one. Notes become issues only if they make a workflow hard to follow.
2. Repeat the failed steps after the fixes, with the same combination.
3. Update [the traceability table](../traceability.md): UI-011, WEB-T08, and WEB-T09 become `Verified`, naming the results file. TEST-015's formal WCAG audit stays deferred past MVP (blueprint 27.4).
4. Update the status note at the top of this file and E.31 decision 18.
5. Rerun the review when navigation, tables, graphs, forms, or kiosk mode change materially.
