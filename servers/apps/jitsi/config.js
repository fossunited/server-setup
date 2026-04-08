// Jitsi Meet configuration.

var config = {};

config.hosts = {};
config.hosts.domain = 'meet.jitsi';

var subdir = '<!--# echo var="subdir" default="" -->';
var subdomain = '<!--# echo var="subdomain" default="" -->';
if (subdir.startsWith('<!--')) {
    subdir = '';
}
if (subdomain) {
    subdomain = subdomain.substring(0,subdomain.length-1).split('.').join('_').toLowerCase() + '.';
}
config.hosts.muc = 'muc.' + subdomain + 'meet.jitsi';
config.bosh = 'https://meet.fossunited.org/' + subdir + 'http-bind';
config.websocket = 'wss://meet.fossunited.org/' + subdir + 'xmpp-websocket';
config.bridgeChannel = {
    preferSctp: true
};


// Video configuration.
//

config.resolution = 720;
config.constraints = {
    video: {
        height: { ideal: 720, max: 720, min: 180 },
        width: { ideal: 1280, max: 1280, min: 320},
    }
};

config.startVideoMuted = 10;
config.startWithVideoMuted = false;

config.flags = {
    sourceNameSignaling: true,
    sendMultipleVideoStreams: true,
    receiveMultipleVideoStreams: true
};

// ScreenShare Configuration.
//

// Audio configuration.
//

config.enableNoAudioDetection = true;
config.enableTalkWhileMuted = false;
config.disableAP = false;
config.disableAGC = false;

config.audioQuality = {
    stereo: false
};

config.startAudioOnly = false;
config.startAudioMuted = 10;
config.startWithAudioMuted = false;
config.startSilent = false;
config.enableOpusRed = false;
config.disableAudioLevels = false;
config.enableNoisyMicDetection = true;


// Peer-to-Peer options.
//

config.p2p = {
    enabled: true,
    codecPreferenceOrder: ["AV1", "VP9", "VP8", "H264"],
    mobileCodecPreferenceOrder: ["VP8", "VP9", "H264", "AV1"]
};

// Breakout Rooms
//

config.hideAddRoomButton = false;


// Etherpad
//

config.etherpad_base = 'https://meet.fossunited.org/etherpad/p/';
// Recording.
//

config.hiddenDomain = 'hidden.meet.jitsi';
config.recordingService = {
    // Whether to enable file recording or not using the "service" defined by the finalizer in Jibri
    enabled: true,

    // Whether to show the possibility to share file recording with other people
    // (e.g. meeting participants), based on the actual implementation
    // on the backend.
    sharingEnabled: false
};

// Live streaming configuration.
config.liveStreaming = {
    enabled: true,
    dataPrivacyLink: 'https://policies.google.com/privacy',
    helpLink: 'https://jitsi.org/live',
    termsLink: 'https://www.youtube.com/t/terms',
    validatorRegExpString: '^(?:[a-zA-Z0-9]{4}(?:-(?!$)|$)){4}'
};


// Local recording configuration.
config.localRecording = {
    disable: false,
    notifyAllParticipants: false,
    disableSelfRecording: false
};


// Analytics.
//

config.analytics = {};

// Dial in/out services.
//


// Calendar service integration.
//

config.enableCalendarIntegration = false;

// Invitation service.
//

// Miscellaneous.
//

// Prejoin page.
config.prejoinConfig = {
    enabled: true,

    // Hides the participant name editing field in the prejoin screen.
    hideDisplayName: false
};

// List of buttons to hide from the extra join options dropdown on prejoin screen.
// Welcome page.
config.welcomePage = {
    disabled: false
};

// Close page.
config.enableClosePage = false;

// Default language.
// Require users to always specify a display name.
config.requireDisplayName = false;

// Chrome extension banner.
// Disables profile and the edit of all fields from the profile settings (display name and email)
config.disableProfile = false;

// Room password (false for anything, number for max digits)
config.roomPasswordNumberOfDigits = false;
// Advanced.
//

// Transcriptions (subtitles and buttons can be configured in interface_config)
config.transcription = {
    enabled: false,
    translationLanguages: [],
    translationLanguagesHead: ['en'],
    useAppLanguage: true,
    preferredLanguage: 'en-US',
    disableStartForAll: false,
    autoCaptionOnRecord: false,
};

// Dynamic branding
// Deployment information.
//

config.deploymentInfo = {};

// Deep Linking
config.disableDeepLinking = false;

// P2P preferred codec
// Video quality settings.
//

config.videoQuality = {};
config.videoQuality.codecPreferenceOrder = ["AV1", "VP9", "VP8", "H264"];
config.videoQuality.mobileCodecPreferenceOrder = ["VP8", "VP9", "H264", "AV1"];
config.videoQuality.enableAdaptiveMode = true;

config.videoQuality.av1 = {};

config.videoQuality.h264 = {};

config.videoQuality.vp8 = {};

config.videoQuality.vp9 = {};

// Reactions
config.disableReactions = false;

// Polls
config.disablePolls = false;

// Configure toolbar buttons
// Hides the buttons at pre-join screen
// Configure remote participant video menu
config.remoteVideoMenu = {
    disabled: false,
    disableKick: false,
    disableGrantModerator: false,
    disablePrivateChat: false
};

// Configure e2eping
config.e2eping = {
    enabled: false
};



// Settings for the Excalidraw whiteboard integration.
config.whiteboard = {
    enabled: true,
collabServerBaseUrl: 'https://whiteboard.meet.fossunited.org'
};

// JaaS support: pre-configure image if JAAS_APP_ID was set.
// Testing
config.testing = {
    enableCodecSelectionAPI: true
};
