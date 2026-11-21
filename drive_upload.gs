// AI Team #1 — Upload ទៅ Google Drive របស់ម្ចាស់
// ដំឡើងម្តង (ម្ចាស់តែប៉ុណ្ណោះ):
//  1. script.google.com → New project → លុបកូដចាស់ ហើយបិទភ្ជាប់ឯកសារនេះ
//  2. ដាក់ FOLDER_ID (link Folder ពេញ ឬតែ ID ក៏បាន) និង SECRET (ពាក្យសម្ងាត់ផ្ទាល់ខ្លួន)
//  3. Deploy → New deployment → Web app · Execute as: Me · Who has access: Anyone → Deploy → Authorize
//     កែកូដក្រោយមក → Deploy → Manage deployments → ✏️ → Version: New version → Deploy (Link ដដែល)
//  4. ចម្លង Web app URL (…/exec) + SECRET ទៅដាក់ក្នុងកម្មវិធី (⚙ ការកំណត់ → ☁ Google Drive)
// កុំដាក់ SECRET ពិតក្នុង Git — ឯកសារនេះមានតែគំរូប៉ុណ្ណោះ
const FOLDER_ID = 'PASTE_FOLDER_ID_HERE';
const SECRET = 'PASTE_YOUR_PASSWORD_HERE';

function doPost(e) {
  let req;
  try {
    req = JSON.parse(e.postData.contents);
  } catch (err) {
    return out({error: 'bad request'});
  }
  if (req.secret !== SECRET) return out({error: 'ពាក្យសម្ងាត់ Google Drive មិនត្រឹមត្រូវ'});
  // បិទភ្ជាប់ link ពេញក៏បាន (…/folders/<ID>?usp=drive_link) — យកតែ ID
  const folderId = (String(FOLDER_ID).match(/folders\/([-\w]+)/) || [null, String(FOLDER_ID).split('?')[0]])[1];
  let folder = DriveApp.getFolderById(folderId);
  if (req.sub) {  // Folder រងតាមឈ្មោះកុំព្យូទ័រមិត្តភក្តិ — ដឹងថាអ្នកណា Upload
    const it = folder.getFoldersByName(req.sub);
    folder = it.hasNext() ? it.next() : folder.createFolder(req.sub);
  }
  const res = UrlFetchApp.fetch(
    'https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&supportsAllDrives=true', {
      method: 'post',
      contentType: 'application/json; charset=UTF-8',
      payload: JSON.stringify({name: req.name, parents: [folder.getId()]}),
      headers: {
        Authorization: 'Bearer ' + ScriptApp.getOAuthToken(),
        'X-Upload-Content-Type': req.mime || 'video/mp4',
        'X-Upload-Content-Length': String(req.size),
      },
      muteHttpExceptions: true,
    });
  if (res.getResponseCode() !== 200) return out({error: 'Drive: ' + res.getContentText().slice(0, 300)});
  const headers = res.getHeaders();
  return out({url: headers['Location'] || headers['location']});
}

function out(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
