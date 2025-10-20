PDF generation note

This project supports server-side PDF generation of a student's answer sheet using WeasyPrint.

- To enable server-side PDF export, install the optional dependency:

  pip install -r requirements.txt

- If WeasyPrint is not installed on the server, the app will fall back to opening the print-friendly HTML page in a new tab and flash a message.

- WeasyPrint may have system dependencies (libpango, libcairo). If you run into installation errors, prefer using the browser Print -> Save as PDF flow.
