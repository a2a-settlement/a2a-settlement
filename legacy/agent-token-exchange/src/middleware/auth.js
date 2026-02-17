const { AccountService } = require('../services/accounts');

// Authenticate via API key in Authorization header
// Format: Authorization: Bearer ate_<key>
async function authenticateBot(req, res, next) {
  try {
    const authHeader = req.headers.authorization;
    if (!authHeader || !authHeader.startsWith('Bearer ')) {
      return res.status(401).json({
        error: 'Missing or invalid Authorization header. Use: Bearer ate_<your_api_key>',
      });
    }

    const apiKey = authHeader.split(' ')[1];
    const account = await AccountService.authenticate(apiKey);

    // Attach authenticated account to request
    req.bot = account;
    next();
  } catch (err) {
    return res.status(err.statusCode || 401).json({ error: err.message });
  }
}

module.exports = { authenticateBot };
