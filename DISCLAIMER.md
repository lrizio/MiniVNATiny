# Disclaimer

miniVNA Tiny is a hobby project by VK3EI, shared for the amateur-radio community. It is an independent, unofficial replacement driver and control program for the mRS miniVNA Tiny. Please read this before relying on it.

- **No warranty.** The software and its results are provided "as is" with no guarantee of accuracy, fitness for any purpose, or freedom from errors. See the MIT licence.
- **Measurements are not certified.** Results depend on the instrument, its calibration standards, cables and connectors, and on how carefully the calibration was done. The derived values (return loss, SWR, impedance, Q, group delay, TDR distance) are calculations from those measurements. Verify anything important with a calibrated instrument. The supply-voltage reading is not trustworthy and is not used anywhere in normal operation.
- **Not for safety-critical use.** Do not use it for anything where life or property depends on the result.
- **Hardware is your responsibility.** Never connect a live transmitter, a charged antenna or any source above the instrument's input rating to the device. The author is not responsible for damaged instruments or equipment.
- **Limitation of liability.** The author is not liable for any loss, damage, injury, interference or licence breach arising from the use of the software or its output.
- **Licence information is not legal advice.**
- **Independence and trademarks.** miniVNA, mRS / mini Radio Solutions, NanoVNA-Saver and other names are mentioned only to describe the hardware this works with or the file formats it exports; there is no affiliation with their owners. The protocol was worked out from published GPL-licensed software and by testing against a real device; no vendor code is included.
- **Privacy.** miniVNA Tiny has no telemetry and no account. Its only network use is an optional update check: shortly after it starts it makes one small request to GitHub's public "latest release" record for this project (GitHub sees your IP address, as for any web request) and shows an "update available" button if a newer version exists; nothing is downloaded or installed, and the **Check for updates** tick box in the top bar turns it off. Settings, calibration and exports stay on your computer, in the folder you run the program from.
- **Unsigned program.** The Windows download is not code-signed, so Windows SmartScreen may warn you. The full source is in this repository.
