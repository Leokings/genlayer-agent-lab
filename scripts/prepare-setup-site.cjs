// Publish only public pages and their icon, never the repository or Lab data.
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const output = path.join(root, '.lab', 'setup-site');
fs.mkdirSync(output, {recursive:true});
// Older emails, kits and installers linked setup sections at the site root.
// Keep those links useful while a plain root visit now shows the home page.
const legacySetup = `<script>
  const setupSections = new Set(['#set-up-lab', '#prepare-contract-test', '#open-dashboard']);
  const setupParams = ['location', 'lab_port', 'local_port', 'ssh_port'];
  if (setupSections.has(location.hash) || setupParams.some(key => new URLSearchParams(location.search).has(key))) {
    location.replace('./setup.html' + location.search + location.hash);
  }
</script>`;
const home = fs.readFileSync(path.join(root, 'docs', 'HOME.html'), 'utf8')
  .replaceAll('./START.html', './setup.html')
  .replace('</head>', `${legacySetup}\n</head>`);
fs.writeFileSync(path.join(output, 'index.html'), home);
const setup = fs.readFileSync(path.join(root, 'docs', 'START.html'), 'utf8')
  .replaceAll('href="https://genlayer-agent-lab-setup.vercel.app/"', 'href="./"');
fs.writeFileSync(path.join(output, 'setup.html'), setup);
fs.copyFileSync(path.join(root, 'docs', 'favicon.svg'), path.join(output, 'favicon.svg'));
fs.writeFileSync(path.join(output, 'vercel.json'), JSON.stringify({
  $schema:'https://openapi.vercel.sh/vercel.json',
  framework:null,
  buildCommand:null,
  outputDirectory:'.',
  redirects:[{source:'/setup', destination:'/setup.html', permanent:true}]
}, null, 2) + '\n');
fs.writeFileSync(path.join(output, '.vercelignore'), '*\n!index.html\n!setup.html\n!favicon.svg\n!vercel.json\n');
console.log('Public home and setup pages prepared in .lab/setup-site. Deploy from that directory.');
