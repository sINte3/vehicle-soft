/* MOBILE-UPLOAD-001: shared multi-file photo/video upload widget.
 *
 * Used by spare_part_form.html (new request items) and spare_part_detail.html
 * (adding attachments to an existing draft/returned-for-revision request).
 * Carries no UI text of its own -- callers pass already-localized strings in
 * `opts.text` so this file stays language-agnostic.
 *
 * Expected markup for one widget instance:
 *   <div class="sp-upload" data-existing-count="0">
 *     <input type="file" class="sp-upload-input" accept="image/*,video/*" multiple>
 *     <button type="button" class="sp-upload-btn">...</button>
 *     <div class="sp-upload-list"></div>
 *     <div class="sp-upload-msg"></div>
 *   </div>
 * Call SpareUpload.init(container, opts) once per widget after it exists in
 * the DOM (on page load for static widgets, right after insertion for rows
 * added dynamically).
 *
 * [REASON]: A native <input type="file" multiple> replaces its whole
 * FileList on every pick, so per-file remove needs the files tracked in a
 * plain JS array and written back into the input via a DataTransfer object --
 * the standard technique for editable multi-file pickers, supported by all
 * evergreen browsers this project targets (Chrome/Firefox/Safari, incl. iOS
 * Safari 14.5+).
 */
(function () {
    'use strict';

    function humanSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    function isImage(file) {
        return (file.type || '').indexOf('image/') === 0;
    }

    function buildFileList(files) {
        var dt = new DataTransfer();
        files.forEach(function (f) { dt.items.add(f); });
        return dt.files;
    }

    function syncInput(widget) {
        widget._input.files = buildFileList(widget._files);
    }

    function showMessage(widget, text) {
        var box = widget.querySelector('.sp-upload-msg');
        if (!box) return;
        box.textContent = text || '';
        box.style.display = text ? 'block' : 'none';
    }

    // [REASON]: after upload, an ancestor form may show a separate "Upload"
    // submit button (spare_part_detail.html) that should only appear once at
    // least one file is staged -- the whole request form on
    // spare_part_form.html has no such button, so this is a no-op there.
    function toggleFormSubmit(widget) {
        var form = widget.closest('form');
        if (!form) return;
        var btn = form.querySelector('.sp-upload-submit');
        if (btn) btn.style.display = widget._files.length ? '' : 'none';
    }

    function renderPreview(widget) {
        var list = widget.querySelector('.sp-upload-list');
        list.innerHTML = '';
        widget._files.forEach(function (file, idx) {
            var row = document.createElement('div');
            row.className = 'sp-upload-item';

            var thumb = document.createElement('div');
            thumb.className = 'sp-upload-thumb';
            if (isImage(file)) {
                var img = document.createElement('img');
                var url = URL.createObjectURL(file);
                img.src = url;
                img.onload = function () { URL.revokeObjectURL(url); };
                thumb.appendChild(img);
            } else {
                thumb.textContent = '🎬'; /* generic file icon for video */
            }

            var meta = document.createElement('div');
            meta.className = 'sp-upload-meta';
            var name = document.createElement('div');
            name.className = 'sp-upload-name';
            name.textContent = file.name;
            name.title = file.name;
            var size = document.createElement('div');
            size.className = 'sp-upload-size';
            size.textContent = humanSize(file.size);
            meta.appendChild(name);
            meta.appendChild(size);

            var remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'sp-upload-remove';
            remove.textContent = '✕';
            remove.addEventListener('click', function () {
                widget._files.splice(idx, 1);
                syncInput(widget);
                renderPreview(widget);
                toggleFormSubmit(widget);
                showMessage(widget, '');
            });

            row.appendChild(thumb);
            row.appendChild(meta);
            row.appendChild(remove);
            list.appendChild(row);
        });
    }

    // [REASON]: remaining slots account for attachments already stored on
    // the server (opts existingCount) plus files already staged this session
    // -- both templates enforce the same 5-per-item cap the server enforces.
    function remainingSlots(widget) {
        return widget._opts.maxFiles - widget._existingCount - widget._files.length;
    }

    function handleChange(widget, picked) {
        var opts = widget._opts;
        var errors = [];
        Array.prototype.forEach.call(picked, function (file) {
            if (remainingSlots(widget) <= 0) {
                /* [REASON]: SP-F-022 — never truncate silently: the message
                 * names WHICH file was not added (fileRejected has {name} and
                 * {max} placeholders); tooMany is kept as a fallback for any
                 * caller not yet passing the new text. The first files that
                 * fit are still kept. */
                var msg = opts.text.fileRejected || opts.text.tooMany || '';
                errors.push(msg.replace('{name}', file.name)
                               .replace('{max}', opts.maxFiles));
                return;
            }
            if (opts.maxSizeBytes && file.size > opts.maxSizeBytes) {
                errors.push((opts.text.tooLarge || '').replace('{name}', file.name));
                return;
            }
            widget._files.push(file);
        });
        syncInput(widget);
        renderPreview(widget);
        toggleFormSubmit(widget);
        showMessage(widget, errors.join(' '));
    }

    function init(container, opts) {
        if (!container || container._spUploadReady) return;
        container._spUploadReady = true;
        var input = container.querySelector('.sp-upload-input');
        var btn = container.querySelector('.sp-upload-btn');
        if (!input || !btn) return;

        container._input = input;
        container._files = [];
        container._opts = Object.assign({maxFiles: 5, maxSizeBytes: 0, text: {}}, opts || {});
        container._existingCount = parseInt(container.dataset.existingCount || '0', 10) || 0;

        if (remainingSlots(container) <= 0) {
            btn.disabled = true;
            showMessage(container, container._opts.text.limitReached || '');
        }

        btn.addEventListener('click', function () { input.click(); });
        input.addEventListener('change', function () {
            handleChange(container, input.files);
        });
    }

    window.SpareUpload = {init: init};
})();
