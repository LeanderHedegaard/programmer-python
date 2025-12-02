netlifyIdentity.on('init', user => {
  if (!user) {
    window.location.href = '/login.html';
  } else if (!user.app_metadata.roles.includes('broker')) {
    alert('Du har ikke adgang til denne side.');
    netlifyIdentity.logout();
  }
});

// Indlæs nummerplader
function loadPlates() {
  let company = document.getElementById("company").value;
  let tableBody = document.getElementById('platesTableBody');
  tableBody.innerHTML = "";

  if (company) {
    fetch("/plates.json")
      .then(response => response.json())
      .then(data => {
        if (data[company]) {
          let localData = JSON.parse(localStorage.getItem(`plates_${company}`)) || {};
          data[company].forEach(plate => {
            let plateData = localData[plate.plate] || plate;
            let row = `<tr class="${plateData.checked ? 'checked' : ''}">
              <td>${plate.plate}</td>
              <td>${plate.date}</td>
              <td>
                <input type="checkbox" ${plateData.checked ? 'checked' : ''} 
                       data-plate="${plate.plate}" 
                       onchange="updatePlate('${company}', '${plate.plate}', this.checked);
                       this.parentElement.parentElement.classList.toggle('checked', this.checked)">
              </td>
              <td>
                <input type="number" value="${plateData.premium || ''}" 
                       data-plate="${plate.plate}" 
                       placeholder="Indtast præmie" 
                       onchange="updatePremium('${company}', '${plate.plate}', this.value)">
              </td>
              <td>
                <button onclick="sendPremium('${company}', '${plate.plate}')" 
                        class="btn btn-primary">Send</button>
              </td>
            </tr>`;
            tableBody.innerHTML += row;
          });
        } else {
          tableBody.innerHTML = "<tr><td colspan='5'>Ingen data</td></tr>";
        }
      })
      .catch(error => console.error("Fejl:", error));
  }
}

// Opdateret sendPremium funktion til at bruge Netlify Forms
function sendPremium(company, plate) {
  const user = netlifyIdentity.currentUser();
  if (!user) {
    alert('Du skal være logget ind for at sende en præmie.');
    return;
  }

  // Sæt brugerens email i skjult felt
  document.getElementById('userInput').value = user.email;

  // Vis modal-formularen
  document.getElementById('plateInput').value = plate;
  $('#premiumModal').modal('show');
}

// Håndter formularindsendelse
document.getElementById('premiumForm').addEventListener('submit', async (e) => {
  e.preventDefault();

  const formData = new FormData(e.target);
  try {
    const response = await fetch('/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams(formData).toString(),
    });

    if (response.ok) {
      alert('Præmie sendt!');
      $('#premiumModal').modal('hide');
      loadPlates(); // Genindlæs data
    } else {
      alert('Fejl ved indsendelse.');
    }
  } catch (error) {
    console.error('Fejl:', error);
    alert('Teknisk fejl - prøv igen senere.');
  }
});

// Eksisterende funktioner
function updatePlate(company, plate, checked) {
  let localData = JSON.parse(localStorage.getItem(`plates_${company}`)) || {};
  localData[plate] = { ...localData[plate], checked, premium: localData[plate]?.premium || 0, timestamp: checked ? new Date().toISOString() : null };
  localStorage.setItem(`plates_${company}`, JSON.stringify(localData));
}

function updatePremium(company, plate, premium) {
  let localData = JSON.parse(localStorage.getItem(`plates_${company}`)) || {};
  localData[plate] = { ...localData[plate], premium: parseFloat(premium) || 0 };
  localStorage.setItem(`plates_${company}`, JSON.stringify(localData));
}

// Initialisering
document.addEventListener('DOMContentLoaded', () => {
  loadPlates();
  netlifyIdentity.on('login', () => loadPlates());
});