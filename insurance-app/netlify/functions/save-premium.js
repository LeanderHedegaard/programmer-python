const fs = require('fs');
const path = require('path');

exports.handler = async function (event, context) {
  // Kun tillad POST-forespørgsler
  if (event.httpMethod !== "POST") {
    return {
      statusCode: 405,
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type",
      },
      body: JSON.stringify({ error: "Method Not Allowed" }),
    };
  }

  // Tjek om brugeren er logget ind
  if (!context.clientContext?.user) {
    return {
      statusCode: 401,
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type",
      },
      body: JSON.stringify({ error: "Ikke autoriseret" }),
    };
  }

  try {
    // Parse request body
    const data = JSON.parse(event.body);
    const { company, plate, premium, user } = data; // Tilføj user her

    // Valider input
    if (!company || !plate || !premium || !user) { // Valider også user
      return {
        statusCode: 400,
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Headers": "Content-Type",
        },
        body: JSON.stringify({
          error: "Manglende felter: company, plate, premium eller user",
        }),
      };
    }

    // Definer stien til platespremium.json
    const filePath = path.join(process.cwd(), 'public', 'platespremium.json');

    // Læs eksisterende data eller initialiser en tom liste
    let platesPremium = [];
    if (fs.existsSync(filePath)) {
      platesPremium = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    }

    // Tilføj den nye præmie til listen
    platesPremium.push({
      company,
      plate,
      premium: parseFloat(premium),
      provision: parseFloat(premium) * 0.02, // Beregn provision (2%)
      timestamp: new Date().toISOString(),
      user, // Gem brugerens email fra formularen
    });

    // Gem den opdaterede liste til filen
    fs.writeFileSync(filePath, JSON.stringify(platesPremium, null, 2));

    // Returner succes
    return {
      statusCode: 200,
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type",
      },
      body: JSON.stringify({
        success: true,
        message: "Præmie gemt succesfuldt!",
        provision: (premium * 0.02).toFixed(2), // Returner provision med 2 decimaler
      }),
    };
  } catch (error) {
    console.error("Fejl:", error);
    return {
      statusCode: 500,
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type",
      },
      body: JSON.stringify({
        error: "Der opstod en fejl ved gemning af præmien.",
      }),
    };
  }
};