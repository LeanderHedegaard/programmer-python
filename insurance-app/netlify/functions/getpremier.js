const fs = require('fs');
const path = require('path');

exports.handler = async () => {
  const filePath = path.join(process.cwd(), 'public', 'platespremium.json');

  if (!fs.existsSync(filePath)) {
    return {
      statusCode: 200,
      body: JSON.stringify([]),
    };
  }

  const rawData = fs.readFileSync(filePath, 'utf8');
  const data = JSON.parse(rawData);

  return {
    statusCode: 200,
    body: JSON.stringify(data),
  };
};