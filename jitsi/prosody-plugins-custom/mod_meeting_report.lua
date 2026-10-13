-- mod_meeting_report.lua
--
-- Persists a per-meeting report when a Jitsi conference room is destroyed.
-- Jitsi rooms are ephemeral: chat and presence are lost when the last
-- participant leaves. This module buffers presence and group chat in memory
-- and, on room destroy, writes:
--
--     <root>/<YYYY-MM-DD>/<room>/attendance.csv
--     <root>/<YYYY-MM-DD>/<room>/chat.txt
--
-- Root defaults to /meetings (override with the meeting_report_root option).
-- Only group chat is captured; private 1:1 messages are never seen here.
-- The same <date>/<room> folder is used by the Jibri finalize script so the
-- recording and metadata land alongside these files. If a same-named room
-- runs twice in one day, a -HHMM suffix is added to the FILE names (not the
-- folder) so nothing is overwritten and both jobs still agree on the folder.
--
-- Breakout rooms live on a SEPARATE MUC component (breakout.<domain>). Load the
-- module there too (XMPP_BREAKOUT_MUC_MODULES=meeting_report) and each breakout
-- gets its OWN folder, "breakout-<name>" (the breakout's subject, or its uuid
-- if the subject is unavailable). Breakouts are kept as separate folders rather
-- than merged into the main room: the breakout component cannot reliably
-- resolve its parent room at destroy time.
--
-- Enable on the conference MUC component by adding "meeting_report" to
-- XMPP_MUC_MODULES, and on the breakout component via XMPP_BREAKOUT_MUC_MODULES.
-- Env changes need `docker compose up -d prosody` (recreate), not `restart`.

local jid = require "util.jid";
local lfs = require "lfs";

local OUTPUT_ROOT = module:get_option_string("meeting_report_root", "/meetings");

-- System participants to keep out of attendance (jicofo focus, Jigasi
-- transcriber, Jibri recorder). Matched case-insensitively against both the
-- display name and the occupant id. Override with meeting_report_exclude.
local EXCLUDE = {};
for _, n in ipairs(module:get_option_array("meeting_report_exclude",
        { "focus", "transcriber", "recorder" })) do
    EXCLUDE[tostring(n):lower()] = true;
end

-- room JID -> { occupants = { [id] = {name, join, leave, talk} },
--               order = { id, ... }, chat = { {time, from, body}, ... },
--               created = <epoch> }
local reports = {};

-- All times reported in IST (UTC+5:30). Container TZ is UTC, so shift the
-- epoch and format as UTC to get IST wall-clock without a tz database.
local IST_OFFSET = 19800; -- 5h30m in seconds

local function now()
    return os.time();
end

local function fmt_time(t)
    return os.date("!%H:%M:%S", t + IST_OFFSET); -- IST, time only
end

local function ist_date(t)
    return os.date("!%Y-%m-%d", t + IST_OFFSET);
end

local function ist_hhmm(t)
    return os.date("!%H%M", t + IST_OFFSET);
end

local function sanitize(name)
    return (tostring(name):gsub("[^%w%._%-]", "_"));
end

-- Folder name for a room. Main rooms use their name (the JID node). Breakout
-- rooms have a uuid node, so prefer the breakout's display name (its subject)
-- and prefix "breakout-". Uses only the node, never the host.
local function room_folder_name(room)
    local node, host = jid.split(room.jid);
    local base = node or "room";
    if host and host:find("breakout") then
        local subject = room.get_subject and room:get_subject();
        if subject and subject ~= "" then base = subject; end
        base = "breakout-" .. base;
    end
    return sanitize(base);
end

-- Create every path component, tolerating races and pre-existing dirs.
local function ensure_dir(path)
    local cur = "";
    for part in path:gmatch("[^/]+") do
        cur = cur .. "/" .. part;
        if not lfs.attributes(cur) then
            local ok, err = lfs.mkdir(cur);
            if not ok and not lfs.attributes(cur) then
                return nil, ("mkdir %s failed: %s"):format(cur, tostring(err));
            end
        end
    end
    return true;
end

-- Collision-safe path: <dir>/<base>.<ext>, or <base>-<suffix>.<ext> if taken.
local function target_file(dir, base, ext, suffix)
    local p = ("%s/%s.%s"):format(dir, base, ext);
    if lfs.attributes(p) then
        p = ("%s/%s-%s.%s"):format(dir, base, suffix, ext);
    end
    return p;
end

local function get_report(room)
    local r = reports[room.jid];
    if not r then
        r = { occupants = {}, order = {}, chat = {}, created = now() };
        reports[room.jid] = r;
    end
    return r;
end

local function occ_id(event)
    local occ = event.occupant;
    if occ and occ.nick then
        return jid.resource(occ.nick) or occ.nick;
    end
    return tostring(event.nick or "?");
end

local function is_system(name, id)
    if name and EXCLUDE[tostring(name):lower()] then return true; end
    if id and EXCLUDE[tostring(id):lower()] then return true; end
    return false;
end

local NICK_NS = "http://jabber.org/protocol/nick";

local function nick_from_stanza(stanza)
    if not stanza then return nil; end
    local n = stanza:get_child_text("nick", NICK_NS);
    if n and n ~= "" then return n; end
    return nil;
end

-- Jitsi guests set their display name in a presence update AFTER joining, so
-- the join stanza often lacks it. Read the occupant's latest stored presence.
local function nick_from_occupant(occupant)
    if occupant and occupant.get_presence then
        local ok, pres = pcall(occupant.get_presence, occupant);
        if ok then return nick_from_stanza(pres); end
    end
    return nil;
end

local function display_name(event)
    local n = nick_from_stanza(event.stanza) or nick_from_occupant(event.occupant);
    if n then return n; end
    local occ = event.occupant;
    if occ and occ.nick then
        local r = jid.resource(occ.nick);
        if r then return r; end
    end
    return "unknown";
end

-- Upgrade a stored name if a real display name is now available.
local function refresh_name(o, occupant)
    if not o then return; end
    local n = nick_from_occupant(occupant);
    if n then o.name = n; end
end

-- Dominant-speaker time (seconds) from mod_speakerstats, if present on the
-- room. Table shape varies by Jitsi version, so probe method + field forms.
-- A person who never held the floor stays at 0 (correct, not a failure).
local function collect_talk(room, r)
    local ss = rawget(room, "speakerStats");
    if type(ss) ~= "table" then return; end
    for key, v in pairs(ss) do
        if type(v) == "table" then
            local ms = 0;
            if type(v.getTotalDominantSpeakerTime) == "function" then
                local ok, res = pcall(v.getTotalDominantSpeakerTime, v);
                if ok and type(res) == "number" then ms = res; end
            elseif type(v.totalDominantSpeakerTime) == "number" then
                ms = v.totalDominantSpeakerTime;
            elseif type(v._totalDominantSpeakerTime) == "number" then
                ms = v._totalDominantSpeakerTime;
            end
            local secs = math.floor(ms / 1000);

            local name = v.displayName or v._displayName;
            if type(v.getDisplayName) == "function" then
                local ok, res = pcall(v.getDisplayName, v);
                if ok and type(res) == "string" then name = res; end
            end

            local o = r.occupants[key];
            if o then
                o.talk = secs;
            elseif name then
                for _, oo in pairs(r.occupants) do
                    if oo.name == name and (oo.talk or 0) == 0 then
                        oo.talk = secs;
                        break;
                    end
                end
            end
        end
    end
end

module:hook("muc-occupant-joined", function(event)
    local room = event.room;
    if not room then return; end
    local r = get_report(room);
    local id = occ_id(event);
    local name = display_name(event);
    if is_system(name, id) then return; end
    if not r.occupants[id] then
        r.occupants[id] = { name = name, join = now(), leave = nil, talk = 0 };
        table.insert(r.order, id);
    end
end);

module:hook("muc-occupant-left", function(event)
    local room = event.room;
    if not room then return; end
    local r = reports[room.jid];
    if not r then return; end
    local o = r.occupants[occ_id(event)];
    if o then
        refresh_name(o, event.occupant);
        if not o.leave then o.leave = now(); end
    end
end);

module:hook("muc-occupant-groupchat", function(event)
    local room = event.room;
    local stanza = event.stanza;
    if not room or not stanza then return; end
    local body = stanza:get_child_text("body");
    if not body or body == "" then return; end
    local r = get_report(room);
    local from = "unknown";
    if event.occupant then
        local o = r.occupants[occ_id(event)];
        if o then
            refresh_name(o, event.occupant);
            from = o.name;
        end
    end
    table.insert(r.chat, { time = now(), from = from, body = body });
end);

module:hook("muc-room-destroyed", function(event)
    local room = event.room;
    if not room then return; end
    local r = reports[room.jid];
    module:log("info", "meeting_report: room destroyed jid=%s tracked=%s",
        tostring(room.jid), r and "yes" or "no");
    if not r then return; end
    reports[room.jid] = nil;

    local ok, err = pcall(function()
        pcall(collect_talk, room, r);

        local room_name = room_folder_name(room);
        local date = ist_date(r.created);
        local suffix = ist_hhmm(r.created);
        local dir = ("%s/%s/%s"):format(OUTPUT_ROOT, date, room_name);

        local made, mkerr = ensure_dir(dir);
        if not made then error(mkerr); end

        -- Refresh names for anyone still present (guests who set their name
        -- after joining), then close them out at destroy time.
        if room.each_occupant then
            for nick, occupant in room:each_occupant() do
                local id = jid.resource(nick) or nick;
                refresh_name(r.occupants[id], occupant);
            end
        end
        local t = now();
        for _, o in pairs(r.occupants) do
            if not o.leave then o.leave = t; end
        end

        -- Merge sessions per person so reconnects don't create duplicate rows.
        -- Key on the trimmed, lowercased name (also folds "Foo "/"foo" variants).
        -- Per person: first join, last leave, total present minutes (summed
        -- across sessions, so gaps are excluded), total talk minutes.
        local agg, agg_order = {}, {};
        for _, id in ipairs(r.order) do
            local o = r.occupants[id];
            if o then
                local disp = (o.name or "unknown"):gsub("^%s+", ""):gsub("%s+$", "");
                local key = disp:lower();
                local a = agg[key];
                if not a then
                    a = { name = disp, first = o.join, last = o.leave or o.join,
                          dur = 0, talk = 0 };
                    agg[key] = a;
                    table.insert(agg_order, key);
                end
                if o.join and o.join < a.first then a.first = o.join; end
                local lv = o.leave or o.join;
                if lv and lv > a.last then a.last = lv; end
                if o.leave and o.join then a.dur = a.dur + (o.leave - o.join); end
                a.talk = a.talk + (o.talk or 0);
            end
        end

        local apath = target_file(dir, "attendance", "csv", suffix);
        local af = io.open(apath, "w");
        if af then
            af:write("name,join,leave,duration_min,talk_min\n");
            for _, key in ipairs(agg_order) do
                local a = agg[key];
                local name = a.name:gsub('"', '""');
                af:write(('"%s",%s,%s,%.1f,%.1f\n'):format(
                    name, fmt_time(a.first), fmt_time(a.last), a.dur / 60, a.talk / 60));
            end
            af:close();
        else
            module:log("error", "meeting_report: cannot write %s", apath);
        end

        if #r.chat > 0 then
            local cpath = target_file(dir, "chat", "txt", suffix);
            local cf = io.open(cpath, "w");
            if cf then
                for _, m in ipairs(r.chat) do
                    cf:write(("[%s] %s: %s\n"):format(fmt_time(m.time), m.from, m.body));
                end
                cf:close();
            else
                module:log("error", "meeting_report: cannot write %s", cpath);
            end
        end

        module:log("info", "meeting_report: wrote report for %s -> %s", room_name, dir);
    end);

    if not ok then
        module:log("error", "meeting_report flush failed: %s", tostring(err));
    end
end);

module:log("info", "mod_meeting_report loaded on %s, output root: %s",
    tostring(module.host), OUTPUT_ROOT);
