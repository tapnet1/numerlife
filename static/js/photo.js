// Profile photo: crop to a square and shrink in the browser before upload
// (this server stalls on uploads over ~120 KB). The server re-checks and re-encodes.
(function () {
  var form = document.querySelector(".photo-form");
  if (!form) { return; }
  var input = form.querySelector("input[type=file]");
  var status = form.querySelector(".photo-status");
  var preview = form.querySelector(".photo-preview");
  input.addEventListener("change", function () {
    var file = input.files && input.files[0];
    if (!file) { return; }
    if (!/^image\//.test(file.type)) { status.textContent = "Please choose a photo (JPG, PNG or WebP)."; return; }
    status.textContent = "Preparing your photo…";
    var url = URL.createObjectURL(file);
    var img = new Image();
    img.onload = function () {
      var side = Math.min(img.naturalWidth, img.naturalHeight);
      var size = Math.min(512, side);
      var canvas = document.createElement("canvas");
      canvas.width = canvas.height = size;
      canvas.getContext("2d").drawImage(img, (img.naturalWidth - side) / 2, (img.naturalHeight - side) / 2, side, side, 0, 0, size, size);
      URL.revokeObjectURL(url);
      var tryQuality = function (q) {
        canvas.toBlob(function (blob) {
          if (blob.size > 90000 && q > 0.5) { return tryQuality(q - 0.1); }
          var dt = new DataTransfer();
          dt.items.add(new File([blob], "photo.jpg", { type: "image/jpeg" }));
          input.files = dt.files;
          if (preview) { preview.src = URL.createObjectURL(blob); preview.hidden = false; }
          status.textContent = "Uploading…";
          form.submit();
        }, "image/jpeg", q);
      };
      tryQuality(0.85);
    };
    img.onerror = function () { status.textContent = "That file couldn't be read as a photo."; };
    img.src = url;
  });
})();
