%  ENTIRE SPIKING ACTIVITY (ESA)
%
%  Based on:
%  Ahmadi et al., Scientific Reports, 2021
%  "Inferring entire spiking activity from local field potentials"
%
%  Processing:
%
%  Raw neural signal
%       |
%       v
%  1st-order Butterworth HPF @ 300 Hz
%       |
%       v
%  Full-wave rectification
%       |
%       v
%  1st-order Butterworth LPF @ 12 Hz
%       |
%       v
%  Downsample to 1 kHz
%       |
%       v
%  Moving average:
%       256 ms window
%       206 ms overlap
%       -> 50 ms step = 20 Hz feature rate
%
%  Butterworth filtering is performed forward and backward using
%  filtfilt(), as described in the paper.
% ========================================================================

clear;
close all;
clc;


%% Input data

% Here I have tested a few neural signals but below is a mock recording to
% test it with
%
% data = readmatrix('neuralData_Andy-2.csv');
% rawNeural = data(:,2);

% Sampling rate from paper
Fs = 24414.0625;       % Hz

% Output sampling frequency in paper
Fs_ESA = 1000;         % Hz

%% Using mock signal:
duration = 10;                         % seconds
t = (0:1/Fs:duration-1/Fs)';

rng(1);

% Low-frequency neural background
rawNeural = ...
      20e-6*sin(2*pi*8*t) ...
    + 10e-6*sin(2*pi*25*t);

% Broadband recording noise
rawNeural = rawNeural + ...
    8e-6*randn(size(t));

spikeRate = ...
    20 + 15*sin(2*pi*0.5*t);

spikeRate(spikeRate < 0) = 0;

spikeEvents = ...
    rand(size(t)) < spikeRate/Fs;


% Simple biphasic EAP
spikeTime = (-0.001:1/Fs:0.001)';

spikeWaveform = ...
    -100e-6 * exp(-(spikeTime/0.00018).^2) ...
    + 45e-6 * exp(-((spikeTime-0.00035)/0.00028).^2);


rawNeural = rawNeural + ...
    conv(double(spikeEvents), ...
         spikeWaveform, ...
         'same');


%% STEP 1 — HIGH-PASS FILTER AT 300 Hz

fc_HP = 300;             % Hz
filterOrder = 1;

[bHP,aHP] = butter( ...
    filterOrder, ...
    fc_HP/(Fs/2), ...
    'high');


% Forward + backward filtering
neural_HP = filtfilt( ...
    bHP, ...
    aHP, ...
    rawNeural);


%% STEP 2 — FULL-WAVE RECTIFICATION

neural_rectified = ...
    abs(neural_HP);


%% STEP 3 — LOW-PASS FILTER AT 12 Hz

fc_LP = 12;              % Hz

[bLP,aLP] = butter( ...
    filterOrder, ...
    fc_LP/(Fs/2), ...
    'low');


% Forward + backward filtering
ESA_raw = filtfilt( ...
    bLP, ...
    aLP, ...
    neural_rectified);


%% STEP 4 — DOWNSAMPLE TO 1 kHz

% Find rational resampling ratio
[p,q] = rat(Fs_ESA/Fs,1e-12);

ESA = resample( ...
    ESA_raw, ...
    p, ...
    q);

t_ESA = (0:length(ESA)-1)' / Fs_ESA;


%% STEP 5 — ESA FEATURE EXTRACTION
%
% Paper:
%
% window  = 256 ms
% overlap = 206 ms
%
% Therefore:
%
% step = 256 - 206
%      = 50 ms
%
% Feature sampling rate = 20 Hz


window_ms  = 256;
overlap_ms = 206;

windowSamples = ...
    round(window_ms/1000 * Fs_ESA);

overlapSamples = ...
    round(overlap_ms/1000 * Fs_ESA);

stepSamples = ...
    windowSamples - overlapSamples;


fprintf('Window size:       %d samples\n',windowSamples);
fprintf('Overlap:           %d samples\n',overlapSamples);
fprintf('Step:              %d samples\n',stepSamples);
fprintf('Feature rate:      %.1f Hz\n',Fs_ESA/stepSamples);


%% MOVING AVERAGE

startSamples = ...
    1:stepSamples:(length(ESA)-windowSamples+1);

Nfeatures = length(startSamples);

ESA_feature = zeros(Nfeatures,1);
t_feature   = zeros(Nfeatures,1);


for k = 1:Nfeatures

    idx = ...
        startSamples(k): ...
        startSamples(k)+windowSamples-1;

    % Rectangular-window moving average
    ESA_feature(k) = ...
        mean(ESA(idx));

    % Time assigned to centre of window
    t_feature(k) = ...
        mean(t_ESA(idx));

end


%% PLOT PROCESSING CHAIN

% Show only a short segment so the spike waveforms are visible
tPlotStart = 2;
tPlotStop  = 3;

idxRaw = ...
    t >= tPlotStart & ...
    t <= tPlotStop;

idxESA = ...
    t_ESA >= tPlotStart & ...
    t_ESA <= tPlotStop;

idxFeature = ...
    t_feature >= tPlotStart & ...
    t_feature <= tPlotStop;


figure('Color','w');

tiledlayout(5,1, ...
    'TileSpacing','compact', ...
    'Padding','compact');

% RAW

nexttile;

plot( ...
    t(idxRaw), ...
    rawNeural(idxRaw)*1e6);

ylabel('Raw (\muV)');

title('ESA extraction — Ahmadi et al. (2021)');

grid on;

% HIGH-PASS

nexttile;

plot( ...
    t(idxRaw), ...
    neural_HP(idxRaw)*1e6);

ylabel('HPF (\muV)');

grid on;

% RECTIFIED

nexttile;

plot( ...
    t(idxRaw), ...
    neural_rectified(idxRaw)*1e6);

ylabel('|HPF| (\muV)');

grid on;

% ESA

nexttile;

plot( ...
    t_ESA(idxESA), ...
    ESA(idxESA)*1e6, ...
    'LineWidth',1.2);

ylabel('ESA (\muV)');

grid on;

% ESA FEATURE

nexttile;

plot( ...
    t_feature(idxFeature), ...
    ESA_feature(idxFeature)*1e6, ...
    '-o', ...
    'LineWidth',1.2, ...
    'MarkerSize',4);

xlabel('Time (s)');
ylabel('ESA feature (\muV)');

grid on;

% COMPLETE ESA PLOT


figure('Color','w');

plot( ...
    t_ESA, ...
    ESA*1e6, ...
    'LineWidth',0.8);

hold on;

plot( ...
    t_feature, ...
    ESA_feature*1e6, ...
    '-o', ...
    'LineWidth',1.5, ...
    'MarkerSize',3);

xlabel('Time (s)');
ylabel('Amplitude (\muV)');

legend( ...
    'ESA', ...
    '256-ms ESA feature', ...
    'Location','best');

title('Entire Spiking Activity');

grid on;
box on;