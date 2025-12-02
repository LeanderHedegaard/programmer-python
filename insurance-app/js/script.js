// Declare netlifyIdentity
const netlifyIdentity = window.netlifyIdentity

netlifyIdentity.on("init", (user) => {
  const loginContainer = document.getElementById("loginContainer")
  const mainContainer = document.querySelector(".container")
  const exportButton = document.getElementById("exportData")

  if (!user) {
    loginContainer.style.display = "block"
    mainContainer.style.display = "none"
    exportButton.style.display = "none"
  } else {
    loginContainer.style.display = "none"
    mainContainer.style.display = "block"
    if (user.app_metadata.roles && user.app_metadata.roles.includes("admin")) {
      exportButton.style.display = "block"
    } else {
      exportButton.style.display = "none"
    }
  }
})

document.addEventListener("DOMContentLoaded", () => {
  const lastUpdatedElement = document.getElementById("lastUpdated")
  const lastUpdatedDate = new Date().toLocaleString()
  lastUpdatedElement.textContent = `Sidst opdateret: ${lastUpdatedDate}`

  const loginButton = document.getElementById("loginButton")
  const loginContainer = document.getElementById("loginContainer")
  const mainContainer = document.querySelector(".container")

  loginButton.addEventListener("click", () => {
    netlifyIdentity.open()
  })

  netlifyIdentity.on("login", (user) => {
    loginContainer.style.display = "none"
    mainContainer.style.display = "block"
    if (user.app_metadata.roles && user.app_metadata.roles.includes("admin")) {
      document.getElementById("exportData").style.display = "block"
    } else {
      document.getElementById("exportData").style.display = "none"
    }
    loadPlates()
  })

  netlifyIdentity.on("logout", () => {
    loginContainer.style.display = "block"
    mainContainer.style.display = "none"
    localStorage.clear()
    window.location.reload()
  })
})

function loadPlates() {
  const company = document.getElementById("company").value
  const tableBody = document.getElementById("platesTableBody")
  tableBody.innerHTML = ""

  if (company) {
    fetch("/plates.json")
      .then((response) => response.json())
      .then((data) => {
        if (data[company]) {
          const localData = JSON.parse(localStorage.getItem(`plates_${company}`)) || {}
          data[company].forEach((plate) => {
            const plateData = localData[plate.plate] || plate
            const row = `<tr class="${plateData.checked ? "checked" : ""}">
              <td>${plate.plate}</td>
              <td>${plate.date}</td>
              <td>
                <input type="checkbox" ${plateData.checked ? "checked" : ""} data-plate="${plate.plate}" onchange="updatePlate('${company}', this.dataset.plate, this.checked); this.parentElement.parentElement.classList.toggle('checked', this.checked)">
              </td>
              <td>
                <input type="number" value="${plateData.premium || 0}" data-plate="${plate.plate}" placeholder="Indtast præmie" onchange="updatePremium('${company}', this.dataset.plate, this.value); updatePremiumSummary('${company}')">
              </td>
              <td>
                <button onclick="sendPremium('${company}', '${plate.plate}')" class="btn btn-primary btn-sm">Send</button>
              </td>
              <td>
                <input type="text" class="form-control" placeholder="">
              </td>
            </tr>`
            tableBody.innerHTML += row
          })

          updatePremiumSummary(company)
        } else {
          tableBody.innerHTML = "<tr><td colspan='6'>Ingen data</td></tr>"
        }
      })
      .catch((error) => console.error("Fejl ved indlæsning af plader:", error))
  }
}

function updatePlate(company, plate, checked) {
  const localData = JSON.parse(localStorage.getItem(`plates_${company}`)) || {}
  localData[plate] = {
    ...localData[plate],
    checked,
    premium: localData[plate]?.premium || 0,
    timestamp: checked ? new Date().toISOString() : null,
  }
  localStorage.setItem(`plates_${company}`, JSON.stringify(localData))
  updatePremiumSummary(company)
}

function updatePremium(company, plate, premium) {
  const localData = JSON.parse(localStorage.getItem(`plates_${company}`)) || {}
  localData[plate] = { ...localData[plate], premium: Number.parseFloat(premium) || 0 }
  localStorage.setItem(`plates_${company}`, JSON.stringify(localData))
  updatePremiumSummary(company)
}

function sendPremium(company, plate) {
  const user = netlifyIdentity.currentUser()
  if (!user) {
    alert("Du skal være logget ind for at sende en præmie.")
    return
  }

  document.getElementById("userInput").value = user.email
  document.getElementById("plateInput").value = plate
  const premiumForm = document.getElementById("premiumForm")
  premiumForm.style.display = "block"
  premiumForm.scrollIntoView({ behavior: "smooth" })
  document.getElementById("premiumInput").focus()
}

document.getElementById("premiumForm").addEventListener("submit", async (e) => {
  e.preventDefault()

  const formData = new FormData(e.target)
  const plate = formData.get("nummerplade")
  const premium = formData.get("premium")
  const user = formData.get("user")

  if (!plate || !premium || !user) {
    alert("Vælg venligst en nummerplade, indtast en præmie, og log ind.")
    return
  }

  try {
    const response = await fetch("/", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        "form-name": "premiumForm",
        nummerplade: plate,
        premium: premium,
        user: user,
      }),
    })

    if (response.ok) {
      alert("Præmie sendt!")
      e.target.reset()
      document.getElementById("premiumForm").style.display = "none"
      loadPlates()
    } else {
      alert("Fejl ved indsendelse af præmie.")
    }
  } catch (error) {
    console.error("Fejl:", error)
    alert("Teknisk fejl - prøv igen senere.")
  }
}

)

function updatePremiumSummary(company) {
  let totalPremium = 0
  const premiumInputs = document.querySelectorAll('input[type="number"]')
  premiumInputs.forEach((input) => {
    totalPremium += Number.parseFloat(input.value) || 0
  })

  let summaryContainer = document.getElementById("premiumSummary")
  if (!summaryContainer) {
    summaryContainer = document.createElement("div")
    summaryContainer.id = "premiumSummary"
    summaryContainer.className = "mt-4 p-3 bg-light rounded border"
    const container = document.querySelector(".container")
    container.appendChild(summaryContainer)
  }

  summaryContainer.innerHTML = `
    <h4>Opsummering</h4>
    <div class="d-flex justify-content-between align-items-center">
      <span>Total præmie:</span>
      <span class="h4 mb-0 text-primary">${totalPremium.toLocaleString("da-DK")} DKK</span>
    </div>
  `
}
