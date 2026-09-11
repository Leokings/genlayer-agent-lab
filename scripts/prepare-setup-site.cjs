// Publish only the public setup document, never the repository or Lab data.
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const output = path.join(root, '.lab', 'setup-site');
fs.mkdirSync(output, {recursive:true});
fs.copyFileSync(path.join(root, 'docs', 'START.html'), path.join(output, 'index.html'));
fs.writeFileSync(path.join(output, 'vercel.json'), JSON.stringify({
  $schema:'https://openapi.vercel.sh/vercel.json',
  framework:null,
  buildCommand:null,
  outputDirectory:'.'
}, null, 2) + '\n');
fs.writeFileSync(path.join(output, '.vercelignore'), '*\n!index.html\n!vercel.json\n');
console.log('Public setup site prepared in .lab/setup-site. Deploy from that directory.');
