netlifyIdentity.on('init', user => {
  if (!user) {
    window.location.href = '/login.html';
  } else if (!user.app_metadata.roles.includes('admin')) {
    alert('Du har ikke adgang til denne side.');
    netlifyIdentity.logout();
  }
});

// Indlæs nummerplader
function loadPlates() {
  let tableBody = document.getElementById('adminTableBody');
  tableBody.innerHTML = '';

  fetch('/plates.json')
    .then(response => response.json())
    .then(data => {
      let allLocalData = {};
      for (let company in data) {
        let localData = JSON.parse(localStorage.getItem(`plates_${company}`)) || {};
        allLocalData[company] = localData;
      }

      for (let company in data) {
        data[company].forEach(plate => {
          let plateData = allLocalData[company]?.[plate.plate] || plate;
          let row = `<tr>
            <td>${plate.plate}</td>
            <td>${plate.date}</td>
            <td>${plateData.checked ? 'Ja' : 'Nej'}</td>
            <td>${plateData.premium || '0'}</td>
            <td>${plateData.user || 'Ukendt'}</td>
            <td>${plateData.timestamp ? new Date(plateData.timestamp).toLocaleString() : ''}</td>
          </tr>`;
          tableBody.innerHTML += row;
        });
      }
    })
    .catch(error => console.error("Fejl ved indlæsning af plader:", error));
}

// Håndter formularindsendelse
document.getElementById('premiumForm').addEventListener('submit', async (e) => {
  e.preventDefault();

  const user = netlifyIdentity.currentUser();
  if (!user) {
    alert('Du skal være logget ind for at sende en præmie.');
    return;
  }

  // Sæt brugerens email i skjult felt
  document.getElementById('userInput').value = user.email;

  const formData = new FormData(e.target);
  try {
    const response = await fetch('/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams(formData).toString(),
    });

    if (response.ok) {
      alert('Præmie sendt!');
      e.target.reset();
      loadPlates(); // Genindlæs data
    } else {
      alert('Fejl ved indsendelse.');
    }
  } catch (error) {
    console.error('Fejl:', error);
    alert('Teknisk fejl - prøv igen senere.');
  }
});

// Importer data
document.getElementById('importData').addEventListener('click', () => {
  $('#importModal').modal('show');
  document.getElementById('submitImport').onclick = () => {
    const jsonData = document.getElementById('importInput').value;
    try {
      const data = JSON.parse(jsonData);
      for (let company in data) {
        localStorage.setItem(`plates_${company}`, JSON.stringify(data[company]));
      }
      loadPlates();
      $('#importModal').modal('hide');
      document.getElementById('importInput').value = '';
      alert('Data importeret succesfuldt.');
    } catch (error) {
      alert('Ugyldig JSON-data.');
    }
  };
});

// Eksporter præmier
document.getElementById('exportPremier').addEventListener('click', async () => {
  try {
    const response = await fetch("/.netlify/functions/get-premier");
    const data = await response.json();
    const ws = XLSX.utils.json_to_sheet(data);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Premier");
    XLSX.writeFile(wb, "premier_med_provision.xlsx");
  } catch (error) {
    console.error("Fejl:", error);
    alert("Kunne ikke hente data.");
  }
});

// Initialisering
document.addEventListener('DOMContentLoaded', () => {
  loadPlates();
  netlifyIdentity.on('login', () => loadPlates());
});