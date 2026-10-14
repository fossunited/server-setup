# Jitsi Meet by FOSS United :: user guide

Free, self-hosted video meetings at **https://meet.fossunited.org**, run by FOSS United and open for orgs and communities to use. Need help? Write to **developers@fossunited.org**.

Official Jitsi usage docs (full walkthrough): https://jitsi.github.io/handbook/docs/user-guide/user-guide-start-a-jitsi-meeting

## Start a meeting

- Open `https://meet.fossunited.org/<your-room>`
  where `<your-room>` is any name you pick, e.g. `https://meet.fossunited.org/my-org` or `https://meet.fossunited.org/org-standup`.

- Any slug works and the room is created the moment someone opens it. Choose a unique, hard-to-guess name, anyone with the link can join.

- Share the link with attendees. No account and no install needed; works in a browser or the Jitsi Meet mobile app.

<figure>
  <img src="https://github.com/user-attachments/assets/a17abbeb-e4b8-463a-b0fc-447202bfd927" alt="Jitsi join screen for meet.fossunited.org/org,
showing the name field and a Join button" title="Joining a meeting" width="700">
  <figcaption>Open <code>meet.fossunited.org/&lt;your-room&gt;</code> and click Join.</figcaption>
</figure>


## Moderator (host)

- The **first person to join becomes moderator** automatically (can mute others, manage the room, start recording).

- If a guest joined before you and you need control, use the FOSS United **fallback moderator token**: open the room with the token link and you become moderator at any time, even mid-meeting.
  Ask **developers@fossunited.org** for the token or if its shared with you by our team.

<figure>
  <img src="https://github.com/user-attachments/assets/931cce87-a6f6-40ea-ba92-f1338950cb39" alt="Moderator more-actions menu listing options
including recording, security options, and settings" title="Moderator options" width="320">
  <figcaption>Moderator options menu (more actions).</figcaption>
</figure>

<figure>
  <img src="https://github.com/user-attachments/assets/17f3f872-16d2-4bb5-ba22-1a173f43b482" alt="Meeting view with moderator settings open and the
participants panel on the right showing the Breakout Rooms option" title="Moderator controls and breakout rooms" width="700">
  <figcaption>Moderator settings, and the Breakout Rooms option in the participants panel.</figcaption>
</figure>


## What you can do

- Screen share, group chat, raise hand, reactions, tile view.

- [Breakout rooms](https://jitsi.support/how-to/jitsi-breakout-rooms/): Breakout rooms are mini sub rooms that is shared and in single meeting where people can jump in an out for small discussions.
  eg: Design thematic, Problem-solving thematic. Makes sense to open for each topic of interest.
  
Open participant panel > "Add breakdown room" button at bottom
  - To join via desktop, open the panel and find the sub-room at bottom with name.
  - To join via mobile app you might need to long press on breakout room to show "join" popup option

- polls, shared notes (Etherpad), collaborative whiteboard.
- Virtual backgrounds and noise suppression.
- Recording and live streaming (moderator only).

For every control and option, see the official handbook linked above.

## Recording

- A moderator can **Start recording** and **Stop recording** from the meeting menu (the three-dots / more actions).
- The recording captures the **meeting video and screen shares only**. It does **not** capture the chat box.

The recordings are saved in our linode server where jitsi runs. You can email us to get the recording for your session. Or you can feel free to screen record yourself as well.

- You can also livestream the meeting directly to youtube by providing the stream key you generated.

## Attendance and chat history (what we save)

- For every meeting we save, privately on our server: the **group chat transcript** and an **attendance list** (who joined, when, and for how long). If the meeting was recorded, the recording is kept with it.

- This is for record-keeping, not public. Only an internal FOSS United technical person can access it, and shares it only when genuinely required, for example when you ask for your own event's attendance.

- **Private 1:1 messages are never saved**, only the public group chat everyone sees.

## Good to know

- Set your display name on the join screen (it is required); it is what shows in the attendance list.
- The meeting room is temporary; it closes once everyone has left, and reopens fresh next time.
  and chat will be gone if everyones leaves.

## Requests, issues, and discussion

- Hit a problem during a meeting? Email **developers@fossunited.org**.
- Want a new feature or want to discuss setup? Open a GitHub issue in the server-setup repo: https://github.com/fossunited/server-setup/issues

## If the site is down

If `meet.fossunited.org` is unreachable, use an alternative for that meeting, for example the official public **https://meet.jit.si/<your-room>** or another service your team prefers, and let us know at **developers@fossunited.org** so we can bring it back.
