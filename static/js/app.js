(function () {
  'use strict';

  let currentConfig = null;
  let pollTimer = null;

  // If the session expires mid-use, every API call starts returning 401. Send the
  // user to the login page rather than letting the UI fail silently.
  const _fetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    return _fetch(input, init).then(function (res) {
      if (res.status === 401) {
        var url = (typeof input === 'string') ? input : (input && input.url) || '';
        if (url.indexOf('/api/') !== -1) {
          window.location = '/login?next=' + encodeURIComponent(window.location.pathname || '/app');
        }
      }
      return res;
    });
  };

  // --- Init ---
  document.addEventListener('DOMContentLoaded', function () {
    loadDefaults();
    refreshConfigList();
    initDropZones();
  });

  // --- Config helpers ---
  function buildConfigFromForm() {
    const divEls = document.querySelectorAll('.div-row');
    const divisions = {};
    const pairRules = {};
    divEls.forEach(function (el) {
      const name = el.querySelector('.div-name').value.trim().toUpperCase();
      if (!name) return;
      divisions[name] = {
        team_count: parseInt(el.querySelector('.div-teams').value) || 6,
        inter: el.querySelector('.div-inter').checked,
        dh_only: el.querySelector('.div-dhonly').checked,
        target_games: parseInt(el.querySelector('.div-target').value) || 14,
        min_dh: parseInt(el.querySelector('.div-mindh').value) || 6,
        max_dh: parseInt(el.querySelector('.div-maxdh').value) || 6,
      };
      pairRules[name] = {
        min: parseInt(el.querySelector('.div-pairmin').value) || 1,
        soft_cap: parseInt(el.querySelector('.div-paircap').value) || 3,
      };
    });

    const interPairs = {};
    document.querySelectorAll('.inter-row').forEach(function (el) {
      interPairs[el.dataset.pair] = {
        enabled: el.querySelector('.inter-enabled').checked,
        degree: parseInt(el.querySelector('.inter-degree').value) || 0,
      };
    });

    const seedVal = document.getElementById('randomSeed').value.trim();

    return {
      divisions: divisions,
      general: {
        weekly_game_limit: parseInt(document.getElementById('weeklyGameLimit').value) || 2,
        home_away_balance: parseInt(document.getElementById('homeAwayBalance').value) || 7,
        hard_min_gap: parseInt(document.getElementById('hardMinGap').value) || 2,
        preferred_min_gap: parseInt(document.getElementById('preferredMinGap').value) || 3,
        // max_retries is vestigial in the scheduler (the backtracking loop it governed
        // was replaced by bounded multi-pass greedy filling); kept for config compatibility.
        max_retries: (currentConfig && currentConfig.general && currentConfig.general.max_retries) || 20000,
        front_load_weeks: parseInt(document.getElementById('frontLoadWeeks').value) || 0,
        max_idle_days: parseInt(document.getElementById('maxIdleDays').value) || 14,
        random_seed: seedVal ? parseInt(seedVal) : null,
        attempts: parseInt(document.getElementById('attempts').value) || 1,
        weekly_soft_target: parseInt(document.getElementById('weeklySoftTarget').value) || null,
        weekly_balance_penalty: (function () {
          var v = document.getElementById('weeklyBalancePenalty').value;
          return v === '' ? 2500 : parseInt(v);
        })(),
      },
      pair_rules: pairRules,
      inter_pairs: interPairs,
      sunday_pod_rotation: document.getElementById('sundayPodRotation').value.split(',').map(function (s) { return s.trim(); }),
      sunday_pods_per_sunday: parseInt(document.getElementById('sundayPodsPerSunday').value) || 3,
      sunday_priority: parseInt(document.getElementById('sundayPriority').value) || 0,
      sunday_pods_only: document.getElementById('sundayPodsOnly').checked,
    };
  }

  // Select an option by value. Configs saved before these became dropdowns (or under
  // the old 0-5000 scale) may hold a value that isn't on the list — rather than
  // silently showing a blank control, surface it as an explicit "Custom" entry.
  function setChoice(id, value, describe) {
    var sel = document.getElementById(id);
    if (!sel) return;
    var v = String(value == null ? '' : value);

    Array.prototype.slice.call(sel.querySelectorAll('option[data-custom]'))
      .forEach(function (o) { o.remove(); });

    var match = Array.prototype.slice.call(sel.options)
      .some(function (o) { return o.value === v; });

    if (!match) {
      var opt = document.createElement('option');
      opt.value = v;
      opt.setAttribute('data-custom', '1');
      opt.textContent = describe ? describe(value) : ('Custom (' + v + ')');
      sel.insertBefore(opt, sel.firstChild);
    }
    sel.value = v;
  }

  function populateForm(cfg) {
    currentConfig = cfg;
    const gen = cfg.general || {};
    document.getElementById('weeklyGameLimit').value = gen.weekly_game_limit || 2;
    document.getElementById('homeAwayBalance').value = gen.home_away_balance || 7;
    document.getElementById('hardMinGap').value = gen.hard_min_gap || 2;
    document.getElementById('preferredMinGap').value = gen.preferred_min_gap || 3;
    document.getElementById('frontLoadWeeks').value = gen.front_load_weeks || 0;
    document.getElementById('maxIdleDays').value = gen.max_idle_days || 14;
    document.getElementById('randomSeed').value = gen.random_seed != null ? gen.random_seed : '';
    document.getElementById('attempts').value = gen.attempts || 1;
    document.getElementById('weeklySoftTarget').value = gen.weekly_soft_target != null ? gen.weekly_soft_target : '';
    setChoice('weeklyBalancePenalty',
      gen.weekly_balance_penalty != null ? gen.weekly_balance_penalty : 2500,
      function (v) { return 'Custom (' + v + ')' + (v > 2500 ? ' — same as Strong' : ''); });
    document.getElementById('sundayPodRotation').value = (cfg.sunday_pod_rotation || ['B', 'C', 'A']).join(',');
    document.getElementById('sundayPodsPerSunday').value = cfg.sunday_pods_per_sunday || 3;
    setChoice('sundayPriority', cfg.sunday_priority || 0);
    document.getElementById('sundayPodsOnly').checked = !!cfg.sunday_pods_only;

    const container = document.getElementById('divisionsContainer');
    container.innerHTML = '';
    const divs = cfg.divisions || {};
    const pr = cfg.pair_rules || {};
    Object.keys(divs).sort().forEach(function (name) {
      const d = divs[name];
      const p = pr[name] || { min: 1, soft_cap: 3 };
      addDivisionRow(name, d, p);
    });

    renderInterPairs(cfg.inter_pairs || {});
    refreshWarnings();
  }

  // Rebuild the inter-division pair grid from the divisions currently on the form,
  // preserving any values already set for pairs that still exist.
  function renderInterPairs(existing) {
    const container = document.getElementById('interPairsContainer');
    if (!container) return;

    // Capture current values before we redraw
    const current = existing || {};
    document.querySelectorAll('.inter-row').forEach(function (el) {
      current[el.dataset.pair] = {
        enabled: el.querySelector('.inter-enabled').checked,
        degree: parseInt(el.querySelector('.inter-degree').value) || 0,
      };
    });

    const names = [];
    document.querySelectorAll('.div-row').forEach(function (el) {
      const n = el.querySelector('.div-name').value.trim().toUpperCase();
      if (n) names.push(n);
    });
    names.sort();

    if (names.length < 2) {
      container.innerHTML = '<div class="text-muted small">Add at least two divisions to configure inter-division play.</div>';
      return;
    }

    let html = '<div class="row g-2">';
    for (let i = 0; i < names.length; i++) {
      for (let j = i + 1; j < names.length; j++) {
        const key = names[i] + '-' + names[j];
        const v = current[key] || { enabled: false, degree: 0 };
        html +=
          '<div class="col-md-4 inter-row" data-pair="' + key + '">' +
            '<div class="card card-body p-2">' +
              '<div class="d-flex align-items-center justify-content-between">' +
                '<div class="form-check mb-0">' +
                  '<input type="checkbox" class="form-check-input inter-enabled" id="inter_' + key + '"' +
                    (v.enabled ? ' checked' : '') + '>' +
                  '<label class="form-check-label fw-bold" for="inter_' + key + '">' + names[i] + ' vs ' + names[j] + '</label>' +
                '</div>' +
                '<div style="width:90px">' +
                  '<input type="number" class="form-control form-control-sm inter-degree" value="' + v.degree + '" min="0"' +
                    ' title="Games per team against that division, on average. 0 means no cross-division games.">' +
                '</div>' +
              '</div>' +
              '<small class="text-muted mt-1">Games per team vs ' + names[j] + '</small>' +
            '</div>' +
          '</div>';
      }
    }
    html += '</div>';
    container.innerHTML = html;

    container.querySelectorAll('.inter-enabled, .inter-degree').forEach(function (el) {
      el.addEventListener('change', refreshWarnings);
    });
  }

  // Ask the server to validate the current form and show any advisory warnings.
  let warnTimer = null;
  function refreshWarnings() {
    clearTimeout(warnTimer);
    warnTimer = setTimeout(function () {
      const box = document.getElementById('configWarnings');
      if (!box) return;
      fetch('/api/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildConfigFromForm()),
      })
        .then(function (r) { return r.json(); })
        .then(function (res) {
          const w = res.warnings || [];
          if (!w.length) { box.innerHTML = ''; return; }
          box.innerHTML = '<div class="alert alert-warning py-2 mb-0"><strong>Heads up:</strong><ul class="mb-0 small">' +
            w.map(function (x) { return '<li>' + x + '</li>'; }).join('') + '</ul></div>';
        })
        .catch(function () { /* validation is advisory only */ });
    }, 250);
  }
  window.refreshWarnings = refreshWarnings;

  // Tooltip text for each division setting. Shown on hover via title attributes.
  const TIPS = {
    name: 'Division letter (A, B, C...). Team names are generated from it: a division "B" with 8 teams gives B1-B8. Must be a single character.',
    teams: 'How many teams are in this division. For doubleheader pods, a multiple of 4 fits best — pods take exactly 4 teams at a time.',
    target: 'Total games each team in this division should play across the season. For a DH-only division this must be even, since every pod gives 2 games.',
    mindh: 'Minimum doubleheader DAYS per team (a DH day = 2 games). The scheduler builds pods until every team reaches this. 6 DH days = 12 of a 14-game season.',
    maxdh: 'Maximum doubleheader days per team. Set equal to Min DH to pin the count exactly. Ignored when DH Only is ticked (every day is a DH day).',
    pairmin: 'Minimum times each pair of teams in this division should meet. The scheduler lowers this automatically if the target does not allow it.',
    paircap: 'Soft ceiling on how often the same two teams meet. The scheduler avoids exceeding it while any pairing is still below Pair Min.',
    inter: 'Allows this division to play cross-division games. Has NO effect on its own — the other division needs it ticked too, AND the pair must be enabled in Inter-Division Play below.',
    dhonly: 'Play ONLY doubleheader pods — never single games. This is the strongest way to force doubleheaders. Needs at least 4 teams, an even Target, and plenty of back-to-back timeslots. Teams may finish short if pods cannot be built.',
  };

  function addDivisionRow(name, d, p) {
    d = d || { team_count: 6, inter: false, dh_only: false, target_games: 14, min_dh: 6, max_dh: 6 };
    p = p || { min: 1, soft_cap: 3 };
    const container = document.getElementById('divisionsContainer');
    const row = document.createElement('div');
    row.className = 'div-row card card-body mb-2 p-2';
    row.innerHTML =
      '<div class="row g-2 align-items-end">' +
        '<div class="col-md-1">' +
          '<label class="form-label help" style="font-size:0.8rem" title="' + TIPS.name + '">Name</label>' +
          '<input type="text" class="form-control form-control-sm div-name" value="' + name + '" maxlength="1" title="' + TIPS.name + '">' +
        '</div>' +
        '<div class="col-md-1">' +
          '<label class="form-label help" style="font-size:0.8rem" title="' + TIPS.teams + '">Teams</label>' +
          '<input type="number" class="form-control form-control-sm div-teams" value="' + d.team_count + '" title="' + TIPS.teams + '">' +
        '</div>' +
        '<div class="col-md-1">' +
          '<label class="form-label help" style="font-size:0.8rem" title="' + TIPS.target + '">Target</label>' +
          '<input type="number" class="form-control form-control-sm div-target" value="' + d.target_games + '" title="' + TIPS.target + '">' +
        '</div>' +
        '<div class="col-md-1">' +
          '<label class="form-label help" style="font-size:0.8rem" title="' + TIPS.mindh + '">Min DH</label>' +
          '<input type="number" class="form-control form-control-sm div-mindh" value="' + d.min_dh + '" title="' + TIPS.mindh + '">' +
        '</div>' +
        '<div class="col-md-1">' +
          '<label class="form-label help" style="font-size:0.8rem" title="' + TIPS.maxdh + '">Max DH</label>' +
          '<input type="number" class="form-control form-control-sm div-maxdh" value="' + d.max_dh + '" title="' + TIPS.maxdh + '">' +
        '</div>' +
        '<div class="col-md-1">' +
          '<label class="form-label help" style="font-size:0.8rem" title="' + TIPS.pairmin + '">Pair Min</label>' +
          '<input type="number" class="form-control form-control-sm div-pairmin" value="' + p.min + '" title="' + TIPS.pairmin + '">' +
        '</div>' +
        '<div class="col-md-1">' +
          '<label class="form-label help" style="font-size:0.8rem" title="' + TIPS.paircap + '">Pair Cap</label>' +
          '<input type="number" class="form-control form-control-sm div-paircap" value="' + p.soft_cap + '" title="' + TIPS.paircap + '">' +
        '</div>' +
        '<div class="col-md-2">' +
          '<div class="form-check mt-3" title="' + TIPS.dhonly + '">' +
            '<input type="checkbox" class="form-check-input div-dhonly"' + (d.dh_only ? ' checked' : '') + '>' +
            '<label class="form-check-label fw-bold" style="font-size:0.8rem">DH Only</label>' +
          '</div>' +
        '</div>' +
        '<div class="col-md-1">' +
          '<div class="form-check mt-3" title="' + TIPS.inter + '">' +
            '<input type="checkbox" class="form-check-input div-inter"' + (d.inter ? ' checked' : '') + '>' +
            '<label class="form-check-label" style="font-size:0.8rem">Inter</label>' +
          '</div>' +
        '</div>' +
        '<div class="col-md-1">' +
          '<button class="btn btn-sm btn-outline-danger" onclick="removeDivision(this)">X</button>' +
        '</div>' +
      '</div>';
    container.appendChild(row);

    // Changing a division name or its flags affects the inter-division grid + warnings
    row.querySelector('.div-name').addEventListener('change', function () {
      renderInterPairs();
      refreshWarnings();
    });
    row.querySelectorAll('input').forEach(function (el) {
      el.addEventListener('change', refreshWarnings);
    });
  }

  // --- Config API ---
  function loadDefaults() {
    fetch('/api/config/defaults')
      .then(function (r) { return r.json(); })
      .then(function (cfg) { populateForm(cfg); });
  }

  function refreshConfigList() {
    fetch('/api/configs')
      .then(function (r) { return r.json(); })
      .then(function (names) {
        var sel = document.getElementById('configSelect');
        sel.innerHTML = '<option value="">-- select --</option>';
        names.forEach(function (n) {
          sel.innerHTML += '<option value="' + n + '">' + n + '</option>';
        });
      });
  }

  window.loadConfig = function () {
    var name = document.getElementById('configSelect').value;
    if (!name) return;
    fetch('/api/configs/' + encodeURIComponent(name))
      .then(function (r) { return r.json(); })
      .then(function (cfg) {
        if (cfg.error) { alert(cfg.error); return; }
        populateForm(cfg);
      });
  };

  window.saveConfig = function () {
    var name = document.getElementById('configName').value.trim();
    if (!name) { alert('Enter a config name'); return; }
    var cfg = buildConfigFromForm();
    fetch('/api/configs/' + encodeURIComponent(name), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cfg),
    })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (res.error) { alert(res.error); return; }
        refreshConfigList();
        document.getElementById('configSelect').value = name;
      });
  };

  window.deleteConfig = function () {
    var name = document.getElementById('configSelect').value;
    if (!name) return;
    if (!confirm('Delete config "' + name + '"?')) return;
    fetch('/api/configs/' + encodeURIComponent(name), { method: 'DELETE' })
      .then(function () { refreshConfigList(); });
  };

  window.addDivision = function () {
    var existing = document.querySelectorAll('.div-row');
    var letters = 'ABCDEFGH';
    var next = letters[existing.length] || 'X';
    addDivisionRow(next);
    renderInterPairs();
    refreshWarnings();
  };

  window.removeDivision = function (btn) {
    btn.closest('.div-row').remove();
    renderInterPairs();
    refreshWarnings();
  };

  // --- File upload ---
  function initDropZones() {
    ['team_availability', 'field_availability', 'team_blackouts'].forEach(function (key) {
      var drop = document.getElementById('drop_' + key);
      var input = document.getElementById('file_' + key);

      input.addEventListener('change', function () {
        if (input.files.length) {
          document.getElementById('fname_' + key).textContent = input.files[0].name;
        }
      });

      drop.addEventListener('dragover', function (e) { e.preventDefault(); drop.classList.add('dragover'); });
      drop.addEventListener('dragleave', function () { drop.classList.remove('dragover'); });
      drop.addEventListener('drop', function (e) {
        e.preventDefault();
        drop.classList.remove('dragover');
        if (e.dataTransfer.files.length) {
          input.files = e.dataTransfer.files;
          document.getElementById('fname_' + key).textContent = e.dataTransfer.files[0].name;
        }
      });
    });
  }

  window.uploadFiles = function () {
    var fd = new FormData();
    var keys = ['team_availability', 'field_availability', 'team_blackouts'];
    for (var i = 0; i < keys.length; i++) {
      var input = document.getElementById('file_' + keys[i]);
      if (!input.files.length) {
        alert('Please select all 3 CSV files');
        return;
      }
      fd.append(keys[i], input.files[0]);
    }

    document.getElementById('uploadStatus').innerHTML = '<span class="spinner-border spinner-border-sm"></span> Uploading...';

    fetch('/api/upload', { method: 'POST', body: fd })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (res.error) {
          document.getElementById('uploadStatus').innerHTML = '<span class="text-danger">' + res.error + '</span>';
          return;
        }
        document.getElementById('uploadStatus').innerHTML = '<span class="text-success">Uploaded successfully</span>';
        var html = '<div class="row g-2">';
        var info = res.uploaded;
        Object.keys(info).forEach(function (k) {
          html += '<div class="col-md-4"><div class="card card-body p-2"><strong>' + k + '</strong><br>' +
            info[k].filename + ' (' + info[k].rows + ' rows)</div></div>';
        });
        html += '</div>';
        document.getElementById('uploadSummary').innerHTML = html;
      })
      .catch(function (err) {
        document.getElementById('uploadStatus').innerHTML = '<span class="text-danger">Upload failed: ' + err + '</span>';
      });
  };

  // --- Run scheduler ---
  window.runScheduler = function () {
    var cfg = buildConfigFromForm();
    var name = (document.getElementById('configName').value.trim() ||
                document.getElementById('configSelect').value || '');
    document.getElementById('btnRun').disabled = true;
    document.getElementById('runSpinner').classList.remove('d-none');
    var logEl = document.getElementById('runLog');
    logEl.classList.remove('d-none');
    logEl.textContent = 'Starting scheduler...\n';

    var attempts = cfg.general.attempts || 1;
    var bar = document.getElementById('runProgress');
    if (attempts > 1) {
      bar.classList.remove('d-none');
      setProgress(0, attempts, null);
    } else {
      bar.classList.add('d-none');
    }

    fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ config: cfg, config_name: name }),
    })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (res.error) {
          logEl.textContent += 'ERROR: ' + res.error + '\n';
          finishRun();
          return;
        }
        // Start the interval BEFORE the first poll: pollStatus clears pollTimer when
        // the run has finished, so polling first would clear a timer that does not
        // exist yet and leave the interval running afterwards.
        if (!res.sync) {
          pollTimer = setInterval(pollStatus, 2000);
        }
        // In synchronous mode the run is already complete when this response
        // arrives, so check immediately instead of waiting out the interval.
        pollStatus();
      })
      .catch(function (err) {
        logEl.textContent += 'Failed to start: ' + err + '\n';
        finishRun();
      });
  };

  function finishRun() {
    document.getElementById('btnRun').disabled = false;
    document.getElementById('runSpinner').classList.add('d-none');
    document.getElementById('runSpinnerText').textContent = 'Running...';
  }

  function setProgress(done, total, bestScore) {
    var pct = total ? Math.round((done / total) * 100) : 0;
    var el = document.getElementById('runProgressBar');
    el.style.width = pct + '%';
    el.textContent = 'Attempt ' + done + ' / ' + total +
      (bestScore != null ? '  (best score ' + bestScore + ')' : '');
    document.getElementById('runSpinnerText').textContent =
      'Attempt ' + done + ' of ' + total + '...';
  }

  function pollStatus() {
    fetch('/api/status')
      .then(function (r) { return r.json(); })
      .then(function (res) {
        var logEl = document.getElementById('runLog');
        if (res.log) logEl.textContent = res.log;
        logEl.scrollTop = logEl.scrollHeight;

        if (res.progress && res.progress.total > 1) {
          document.getElementById('runProgress').classList.remove('d-none');
          setProgress(res.progress.done, res.progress.total, res.progress.best_score);
        }

        if (res.status === 'done' || res.status === 'error') {
          clearInterval(pollTimer);
          pollTimer = null;
          finishRun();
          document.getElementById('runProgress').classList.add('d-none');

          if (res.status === 'done') {
            loadResults();
          }
        }
      });
  }

  function loadResults() {
    fetch('/api/results')
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (res.error) return;

        document.getElementById('resultsPlaceholder').classList.add('d-none');
        document.getElementById('resultsContent').classList.remove('d-none');

        // Open results accordion
        var resultsPanel = document.getElementById('panelResults');
        if (!resultsPanel.classList.contains('show')) {
          new bootstrap.Collapse(resultsPanel, { toggle: true });
        }

        renderStats(res.stats);
        renderWarnings(res.warnings);
        renderDownloads(res.output_files);
        renderTeamStats(res.stats.per_team);
        renderWeeklyTable(res.weekly_table);
        renderMatchupMatrix(res.matchup_matrix);
        renderSchedule(res.schedule_preview);
      });
  }

  function renderStats(stats) {
    var shortCls = stats.games_short > 0 ? ' text-danger' : ' text-success';
    var html =
      '<div class="col-md-3"><div class="card stat-card p-3"><div class="text-muted">Total Games</div><h3>' + stats.total_games + '</h3></div></div>' +
      '<div class="col-md-3"><div class="card stat-card p-3"><div class="text-muted">Games Short of Target</div><h3 class="' + shortCls.trim() + '">' + (stats.games_short != null ? stats.games_short : '-') + '</h3></div></div>' +
      '<div class="col-md-3"><div class="card stat-card p-3"><div class="text-muted">Idle Weeks</div><h3>' + (stats.idle_weeks != null ? stats.idle_weeks : '-') + '</h3></div></div>' +
      '<div class="col-md-3"><div class="card stat-card p-3"><div class="text-muted">Overloaded Weeks</div><h3>' + (stats.heavy_weeks != null ? stats.heavy_weeks : '-') + '</h3></div></div>';

    if (stats.worst_idle_gap != null) {
      var gapCls = stats.idle_violations > 0 ? 'text-danger' : 'text-success';
      html +=
        '<div class="col-md-3"><div class="card stat-card p-3"><div class="text-muted">Longest Layoff</div>' +
        '<h3 class="' + gapCls + '">' + stats.worst_idle_gap + 'd</h3>' +
        '<small class="text-muted">target ' + (stats.max_idle_days || 14) + 'd &middot; ' +
        stats.idle_violations + ' over</small></div></div>';
    }

    if (stats.attempts_run > 1) {
      html += '<div class="col-12"><div class="alert alert-info py-2 mb-0 small">' +
        'Ran <strong>' + stats.attempts_run + '</strong> attempts. Best was seed <strong>' +
        stats.best_seed + '</strong> (score ' + stats.best_score + ', lower is better). ' +
        'Re-enter that seed under Random Seed to reproduce this exact schedule.' +
        '</div></div>';
    }
    document.getElementById('statsCards').innerHTML = html;
  }

  function renderWarnings(warnings) {
    var box = document.getElementById('configWarnings');
    if (!box) return;
    if (!warnings || !warnings.length) { box.innerHTML = ''; return; }
    box.innerHTML = '<div class="alert alert-warning py-2 mb-0"><strong>Heads up:</strong><ul class="mb-0 small">' +
      warnings.map(function (w) { return '<li>' + w + '</li>'; }).join('') + '</ul></div>';
  }

  function renderDownloads(files) {
    var html = '<h6 class="fw-bold">Downloads</h6>';
    if (files.xlsx) html += '<a href="/api/download/' + files.xlsx + '" class="btn btn-success me-2">Download XLSX</a>';
    if (files.csv) html += '<a href="/api/download/' + files.csv + '" class="btn btn-outline-secondary me-2">Download CSV</a>';
    if (files.unscheduled) html += '<a href="/api/download/' + files.unscheduled + '" class="btn btn-outline-secondary me-2">Unscheduled Matchups</a>';
    if (files.remaining) html += '<a href="/api/download/' + files.remaining + '" class="btn btn-outline-secondary">Remaining Needs</a>';
    if (files.xlsx) html += '<div class="small text-muted mt-2">Saved as <code>' + files.xlsx + '</code></div>';
    document.getElementById('downloadButtons').innerHTML = html;
  }

  function renderTeamStats(perTeam) {
    var tbody = document.querySelector('#teamStatsTable tbody');
    tbody.innerHTML = '';
    var teams = Object.keys(perTeam).sort();
    teams.forEach(function (t) {
      var s = perTeam[t];
      var cls = s.total < s.target ? ' class="table-warning"' : '';
      var pd = (s.playable_dates != null) ? s.playable_dates : '-';
      tbody.innerHTML += '<tr' + cls + '><td>' + t + '</td><td>' + t[0] + '</td><td>' + s.total +
        '</td><td>' + s.target + '</td><td>' + s.home + '</td><td>' + s.away +
        '</td><td>' + s.dh_days + '</td><td>' + pd + '</td></tr>';
    });
  }

  // State mapping for the weekly grid, kept as a pure function so the
  // thresholds are readable in one place and can be exercised directly.
  //
  // Low-capacity weeks return null: there are too few diamonds for everyone to
  // play, so a team sitting out is not a scheduling fault and marking it idle
  // would cry wolf.
  //
  // On pace carries no marker on purpose. It is the majority of cells, and
  // flagging every one of them turns the grid into noise; the deviations are
  // what need to catch the eye. The count itself already separates idle (0)
  // from the rest without relying on colour.
  function weekStatus(count, target, lowCapacity) {
    if (lowCapacity) return null;
    if (count === 0) {
      return { cls: 'status-idle', flag: '○', label: 'idle' };
    }
    if (count > target) {
      return { cls: 'status-over-target', flag: '!', label: 'over the weekly target of ' + target };
    }
    return { cls: 'status-on-pace', flag: '', label: 'on pace' };
  }

  function renderWeeklyTable(wt) {
    var thead = document.querySelector('#weeklyTable thead');
    var tbody = document.querySelector('#weeklyTable tbody');
    thead.innerHTML = '';
    tbody.innerHTML = '';
    if (!wt || !wt.weeks || !wt.weeks.length) return;

    var header = '<tr><th class="row-head">Team</th>';
    wt.weeks.forEach(function (w) {
      var cls = w.low_capacity ? 'lowcap' : '';
      var cap = w.slots != null
        ? '  |  ' + w.slots + ' slots = ' + Math.floor(w.slots / 2) + ' games, seats ' + w.slots + ' team-games'
        : '';
      var tip = 'Week ' + w.index + ', starting ' + w.starts + cap +
        (w.low_capacity
          ? '  |  Too few diamonds this week to give every team a game, so teams sitting out is unavoidable.'
          : '');
      header += '<th class="' + cls + '" title="' + tip + '">w' + w.index + '</th>';
    });
    header += '<th>Total</th></tr>';
    thead.innerHTML = header;

    wt.teams.forEach(function (t) {
      var counts = wt.counts[t] || [];
      var target = (wt.soft_target && wt.soft_target[t]) || 2;
      var row = '<tr><td class="row-head">' + t + '</td>';
      var total = 0;
      counts.forEach(function (c, i) {
        total += c;
        var week = wt.weeks[i];
        var st = weekStatus(c, target, week && week.low_capacity);
        if (!st) {
          row += '<td>' + c + '</td>';
          return;
        }
        // Colour is backed up by the marker glyph, the tooltip and the
        // screen-reader text, so the state survives without it.
        var wk = week ? 'w' + week.index : 'week ' + (i + 1);
        row += '<td class="' + st.cls + '" title="' + wk + ': ' + c +
          (c === 1 ? ' game' : ' games') + ', ' + st.label + '">' + c +
          (st.flag ? '<span class="wk-flag" aria-hidden="true">' + st.flag + '</span>' : '') +
          '<span class="visually-hidden">, ' + st.label + '</span></td>';
      });
      row += '<td><strong>' + total + '</strong></td></tr>';
      tbody.innerHTML += row;
    });

    tbody.innerHTML +=
      '<tr><td colspan="' + (wt.weeks.length + 2) + '" class="small text-muted" style="text-align:left">' +
      '<span class="badge status-on-pace">on pace</span> ' +
      '<span class="badge status-over-target">above weekly target ' +
        '<span class="wk-flag" aria-hidden="true">!</span></span> ' +
      '<span class="badge status-idle">idle week ' +
        '<span class="wk-flag" aria-hidden="true">○</span></span> ' +
      '<span class="badge" style="background:var(--surface);color:var(--ink-600)">' +
        'grey column = too few diamonds that week</span>' +
      '</td></tr>';
  }

  function renderMatchupMatrix(mm) {
    var thead = document.querySelector('#matchupMatrix thead');
    var tbody = document.querySelector('#matchupMatrix tbody');
    thead.innerHTML = '';
    tbody.innerHTML = '';
    if (!mm || !mm.teams || !mm.teams.length) return;

    var teams = mm.teams;
    var divs = mm.divisions || {};
    var grid = mm.grid || {};

    // Header row
    var header = '<tr><th class="row-head"></th>';
    teams.forEach(function (t, i) {
      var sep = (i > 0 && divs[t] !== divs[teams[i - 1]]) ? ' divsep' : '';
      header += '<th class="' + sep.trim() + '">' + t + '</th>';
    });
    header += '</tr>';
    thead.innerHTML = header;

    // Body rows
    teams.forEach(function (rowTeam) {
      var row = '<tr><td class="row-head">' + rowTeam + '</td>';
      teams.forEach(function (colTeam, i) {
        var sep = (i > 0 && divs[colTeam] !== divs[teams[i - 1]]) ? ' divsep' : '';
        if (rowTeam === colTeam) {
          row += '<td class="diag' + sep + '"></td>';
        } else {
          var v = (grid[rowTeam] && grid[rowTeam][colTeam]) || 0;
          var cls = v === 0 ? 'zero' : (v >= 3 ? 'hot' : '');
          row += '<td class="' + (cls + sep).trim() + '">' + (v || '') + '</td>';
        }
      });
      row += '</tr>';
      tbody.innerHTML += row;
    });
  }

  function renderSchedule(preview) {
    var tbody = document.querySelector('#scheduleTable tbody');
    tbody.innerHTML = '';
    preview.forEach(function (g) {
      tbody.innerHTML += '<tr><td>' + g.date + '</td><td>' + g.day + '</td><td>' + g.time +
        '</td><td>' + g.field + '</td><td><span class="badge bg-primary badge-div">' + g.home_div +
        '</span> ' + g.home + '</td><td><span class="badge bg-secondary badge-div">' + g.away_div +
        '</span> ' + g.away + '</td></tr>';
    });
  }

})();
