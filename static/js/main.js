// main.js

// --- Global/module-level variables for DOM elements ---
let logOutputElement = null;
let loaderElement = null;
let videoStreamImgElement = null;

// --- Configuration variables (to be populated from HTML) ---
let ONVIF_HOST_CONFIG;
let ONVIF_USER_CONFIG;
let ONVIF_PASSWORD_CONFIG;
let RTSP_URL_CONFIG;
let SELECTED_CAMERA_TYPE_NAME_CONFIG;
let IS_INVERT_UPDOWN_PTZ_CONFIG;

// --- PTZ constants ---
const PTZ_MOVE_TIME_MS = 0.4 * 1000; // Movement duration in milliseconds
const PAN_SPEED_CONFIG = 0.5;
const TILT_SPEED_CONFIG = 0.5;
const ZOOM_SPEED_CONFIG = 0.5;

/// --- Backend URL ---
const BACKEND_BASE_URL = 'http://localhost:5000'; // Replace if your backend is on a different address/port

// --- Logging and UI functions ---
function logger(message, type = 'info') {
    if (!logOutputElement) {
        console[type === 'error' ? 'error' : 'log'](`(Log element not ready) ${type.toUpperCase()}: ${message}`);
        return;
    }
    const now = new Date().toLocaleTimeString();
    const p = document.createElement('p');
    p.textContent = `[${now}] ${type.toUpperCase()}: ${message}`;

    if (type === 'error') p.style.color = 'red';
    else if (type === 'success') p.style.color = 'green';

    logOutputElement.appendChild(p);
    logOutputElement.scrollTop = logOutputElement.scrollHeight;
    console[type === 'error' ? 'error' : 'log'](message);
}

function showLoader() {
    if (loaderElement) {
        loaderElement.style.display = 'block';
    } else {
        console.warn("The 'loader' element was not found. Unable to display.");
    }
}

function hideLoader() {
    if (loaderElement) {
        loaderElement.style.display = 'none';
    } else {
        console.warn("The 'loader' element was not found. Cannot hide it.");
    }
}

// --- Functions for ONVIF PTZ control ---
const CAMERA_PARAMS_CONFIG = {
    YOOSEE: { profile: "IPCProfilesToken1", servicePath: ":5000/onvif/ptz_service" },
    YCC365: { profile: "Profile_1", servicePath: "/onvif/PTZ" },
    Y05:    { profile: "PROFILE_000", servicePath: ":6688/onvif/ptz_service" }
};

function getCameraOnvifParamsDetail(cameraType) {
    return CAMERA_PARAMS_CONFIG[cameraType] || null;
}

function generateNonceForOnvif(length = 24) {
    let result = '';
    const characters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    for (let i = 0; i < length; i++) {
        result += characters.charAt(Math.floor(Math.random() * characters.length));
    }
    return result;
}

async function generateOnvifWsSecurityHeaderJS(user, password) {
    const createdDate = new Date().toISOString();
    const nonceStr = generateNonceForOnvif();
    const nonceBase64 = btoa(nonceStr);

    const encoder = new TextEncoder();
    const nonceBytes = encoder.encode(nonceStr);
    const createdBytes = encoder.encode(createdDate);
    const passwordBytes = encoder.encode(password);

    const combinedData = new Uint8Array(nonceBytes.length + createdBytes.length + passwordBytes.length);
    combinedData.set(nonceBytes, 0);
    combinedData.set(createdBytes, nonceBytes.length);
    combinedData.set(passwordBytes, nonceBytes.length + createdBytes.length);

    const hashBuffer = await crypto.subtle.digest('SHA-1', combinedData);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    const passwordDigest = btoa(String.fromCharCode(...hashArray));

    return `
    <s:Header>
        <Security s:mustUnderstand="1" xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
            <UsernameToken>
                <Username>${user}</Username>
                <Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">${passwordDigest}</Password>
                <Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">${nonceBase64}</Nonce>
                <Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">${createdDate}</Created>
            </UsernameToken>
        </Security>
    </s:Header>`;
}

// This function is no longer needed on the frontend, as the backend constructs the SOAP.
// It has been left here for reference, in case the logic were different.
/*
async function buildOnvifEnvelopeJS(user, password, serviceContent, cameraType) {
    // ...
}
*/

async function sendOnvifRequestToBackend(ptzAction, panSpeed = 0, tiltSpeed = 0, zoomSpeed = 0) {
    showLoader();
    const requestUrl = `${BACKEND_BASE_URL}/api/ptz`;
    const payload = {
        camera_ip: ONVIF_HOST_CONFIG,
        onvif_user: ONVIF_USER_CONFIG,
        onvif_password: ONVIF_PASSWORD_CONFIG,
        camera_type: SELECTED_CAMERA_TYPE_NAME_CONFIG,
        action: ptzAction
    };

    if (ptzAction === 'move') {
        payload.pan = panSpeed;
        payload.tilt = tiltSpeed;
        payload.zoom = zoomSpeed;
    }

    logger(`Sending a PTZ command: ${ptzAction} on ${requestUrl}`);

    try {
        const response = await fetch(requestUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });

        const responseData = await response.json();

        if (response.ok && responseData.status === 'success') {
            logger(`PTZ ${ptzAction} successful: ${responseData.message}`, 'success');
        } else {
            const errorMsg = responseData.message || `HTTP ${response.status}`;
            logger(`Error PTZ ${ptzAction}: ${errorMsg}. Details: ${responseData.details || ''}`, 'error');
        }
    } catch (e) {
        logger(`Exception when PTZ ${ptzAction}: ${e.message}`, 'error');
    } finally {
        hideLoader();
    }
}

async function ptzStopRequestCommand() {
    await sendOnvifRequestToBackend('stop');
}

async function ptzContinuousMoveRequestCommand(x, y, z) {
    await sendOnvifRequestToBackend('move', x, y, z);
}

// --- PTZ motion state control and auto-stop ---
let ptzMoveTimeoutId = null;
let isCurrentlyMovingPtz = false;

function startPtzMovement(x, y, z) {
    if (ptzMoveTimeoutId) clearTimeout(ptzMoveTimeoutId);
    isCurrentlyMovingPtz = true;
    ptzContinuousMoveRequestCommand(x, y, z);
    ptzMoveTimeoutId = setTimeout(() => {
        if (isCurrentlyMovingPtz) {
            logger("Авто-остановка PTZ движения.");
            ptzStopRequestCommand(); // We use a command for the backend
            isCurrentlyMovingPtz = false;
        }
    }, PTZ_MOVE_TIME_MS);
}

function stopPtzMovement() {
    if (ptzMoveTimeoutId) clearTimeout(ptzMoveTimeoutId);
    if (isCurrentlyMovingPtz) {
        ptzStopRequestCommand(); // We use a command for the backend
        isCurrentlyMovingPtz = false;
    }
}

// --- Updating configuration from HTML ---
function updateAllConfigValues() {
    ONVIF_HOST_CONFIG = document.getElementById('cameraIp').value;
    ONVIF_USER_CONFIG = document.getElementById('onvifUser').value;
    ONVIF_PASSWORD_CONFIG = document.getElementById('onvifPassword').value;
    RTSP_URL_CONFIG = document.getElementById('rtspUrl').value;
    SELECTED_CAMERA_TYPE_NAME_CONFIG = document.getElementById('cameraType').value;
    IS_INVERT_UPDOWN_PTZ_CONFIG = document.getElementById('invertUpDown').checked;
    logger("Configuration updated from HTML fields.");
}

// --- Initialization and event handlers ---
document.addEventListener('DOMContentLoaded', () => {
    // Initialization of DOM elements
    logOutputElement = document.getElementById('logOutput');
    loaderElement = document.getElementById('loader');
    videoStreamImgElement = document.getElementById('videoStream');

    // Checks for the presence of critical elements
    if (!logOutputElement) {
        console.error("CRITICAL ERROR: HTML element with ID 'logOutput' not found!");
        alert("Error: element for log output not found.");
    }
    if (!loaderElement) {
        console.error("CRITICAL ERROR: HTML element with ID 'loader' not found!");
        alert("Error: loading indicator element not found.");
    }
    if (!videoStreamImgElement) {
        console.error("CRITICAL ERROR: HTML element with ID 'videoStream' not found!");
        alert("Error: element for video display not found.");
    }

    updateAllConfigValues(); // Initial reading of the config

    // Updating the config when fields change
    ['cameraIp', 'onvifUser', 'onvifPassword', 'rtspUrl', 'cameraType', 'invertUpDown'].forEach(id => {
        const element = document.getElementById(id);
        if (element) {
            element.addEventListener('change', updateAllConfigValues);
        }
    });

    // --- Handlers for PTZ buttons ---
    const ptzControlConfig = [
        { id: 'ptzUp',    x: 0, y: () => IS_INVERT_UPDOWN_PTZ_CONFIG ? -TILT_SPEED_CONFIG : TILT_SPEED_CONFIG, z: 0, key: 'w' },
        { id: 'ptzDown',  x: 0, y: () => IS_INVERT_UPDOWN_PTZ_CONFIG ? TILT_SPEED_CONFIG : -TILT_SPEED_CONFIG, z: 0, key: 's' },
        { id: 'ptzLeft',  x: -PAN_SPEED_CONFIG, y: 0, z: 0, key: 'a' },
        { id: 'ptzRight', x: PAN_SPEED_CONFIG,  y: 0, z: 0, key: 'd' },
        { id: 'ptzZoomIn',x: 0, y: 0, z: ZOOM_SPEED_CONFIG, key: 'z' },
        { id: 'ptzZoomOut',x:0, y: 0, z: -ZOOM_SPEED_CONFIG,key: 'x' }
    ];

    ptzControlConfig.forEach(ctrl => {
        const button = document.getElementById(ctrl.id);
        if (button) {
            const action = () => startPtzMovement(ctrl.x, typeof ctrl.y === 'function' ? ctrl.y() : ctrl.y, ctrl.z);
            button.addEventListener('mousedown', action);
            button.addEventListener('touchstart', (e) => { e.preventDefault(); action(); }, { passive: false });

            button.addEventListener('mouseup', stopPtzMovement);
            button.addEventListener('mouseleave', stopPtzMovement);
            button.addEventListener('touchend', stopPtzMovement);
            button.addEventListener('touchcancel', stopPtzMovement);
        } else {
            logger(`PTZ button with ID '${ctrl.id}' not found.`, 'error');
        }
    });

    const stopButton = document.getElementById('ptzStop');
    if (stopButton) {
        stopButton.addEventListener('click', stopPtzMovement);
    } else {
        logger("'ptzStop' button not found.", 'error');
    }

    // --- Keyboard shortcuts ---
    document.addEventListener('keydown', (event) => {
        if (document.activeElement && ['input', 'select', 'textarea'].includes(document.activeElement.tagName.toLowerCase())) {
            return; // Do not process if the focus is on an input
        }
        const control = ptzControlConfig.find(c => c.key === event.key.toLowerCase());
        if (control && !isCurrentlyMovingPtz) {
            event.preventDefault();
            startPtzMovement(control.x, typeof control.y === 'function' ? control.y() : control.y, control.z);
        } else if (event.key === ' ' || event.code === 'Space') {
            event.preventDefault();
            stopPtzMovement();
        }
    });
    document.addEventListener('keyup', (event) => {
        if (document.activeElement && ['input', 'select', 'textarea'].includes(document.activeElement.tagName.toLowerCase())) {
            return;
        }
        const control = ptzControlConfig.find(c => c.key === event.key.toLowerCase());
        if (control && isCurrentlyMovingPtz) {
            event.preventDefault();
            stopPtzMovement();
        }
    });

    // --- Video stream start/refresh button ---
    const startStreamButton = document.getElementById('startStreamButton');
    if (startStreamButton && videoStreamImgElement) {
        startStreamButton.addEventListener('click', () => {
            const currentRtspUrl = document.getElementById('rtspUrl').value;
            if (!currentRtspUrl) {
                logger("RTSP URL is not specified.", "error");
                alert("Please enter the RTSP URL for the video stream.");
                return;
            }
            const mjpegStreamUrl = `${BACKEND_BASE_URL}/api/video-stream?rtsp=${encodeURIComponent(currentRtspUrl)}`;
            logger(`Attempting to load a video from: ${mjpegStreamUrl}`);
            videoStreamImgElement.src = mjpegStreamUrl;
            videoStreamImgElement.onerror = () => {
                logger("Error loading video stream. Ensure that the backend is configured, the camera is accessible, and the RTSP URL is correct.", "error");
                videoStreamImgElement.src = "https://placehold.co/640x360/000000/FFFFFF?text=Video+loading+error%0A(check+the+URL+and+backend)";
            };
            videoStreamImgElement.onload = () => {
                 logger("The video stream has been successfully loaded (or loading has begun).", "success");
            };
        });
    } else {
        logger("The 'startStreamButton' button or the 'videoStream' element could not be found.", 'error');
    }

    logger("Interface initialized. DOM loaded.");
    hideLoader(); // Let's ensure that the loader is hidden during the initial load.
});