// Old links pointed at the dashboard root (e.g. /#machine/CMP-00430); forward them to the dashboard subpage.
if (location.hash.length > 1) location.replace("/dashboard/" + location.hash);
